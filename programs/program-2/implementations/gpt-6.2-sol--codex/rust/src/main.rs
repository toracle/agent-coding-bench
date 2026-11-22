mod store;

use clap::{Parser, Subcommand};
use serde_json::{json, Value};
use std::io::Read;
use std::sync::Arc;
use tiny_http::{Header, Method, Request, Response, Server};

use store::{error, Reply, Result, MAX_INPUT};

#[derive(Parser)]
struct ServerArguments {
    #[command(subcommand)]
    command: ServerCommand,
    #[arg(
        long,
        env = "DATABASE_URL",
        default_value = "sqlite:///inventory.db",
        global = true
    )]
    database_url: String,
    #[arg(long, default_value_t = 8080, global = true)]
    port: u16,
    #[arg(long, default_value = "127.0.0.1", global = true)]
    host: String,
    #[arg(long, global = true)]
    benchmark_mode: bool,
    #[arg(long, default_value_t = 10, global = true)]
    stock_a: i32,
    #[arg(long, default_value_t = 5, global = true)]
    stock_b: i32,
}

#[derive(Subcommand)]
enum ServerCommand {
    Migrate,
    Seed,
    Serve,
}

#[derive(Parser)]
struct ClientArguments {
    #[arg(long, env = "BASE_URL", default_value = "http://127.0.0.1:8080")]
    base_url: String,
    #[command(subcommand)]
    command: ClientCommand,
}

#[derive(Subcommand)]
enum ClientCommand {
    Health,
    Stock {
        sku: String,
    },
    Reservation {
        id: String,
    },
    Reserve {
        #[arg(long, required = true)]
        item: Vec<String>,
        #[arg(long)]
        ttl: i32,
        #[arg(long)]
        request_id: String,
    },
    Confirm {
        id: String,
        #[arg(long)]
        request_id: String,
    },
    Cancel {
        id: String,
        #[arg(long)]
        request_id: String,
    },
    Advance {
        seconds: i32,
        #[arg(long)]
        request_id: String,
    },
}

fn identifier(value: &str, limit: usize) -> bool {
    !value.is_empty()
        && value.len() <= limit
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
}

fn sku(value: &str) -> bool {
    identifier(value, 32) && !value.bytes().any(|b| b.is_ascii_lowercase())
}

fn number(value: Option<&Value>) -> Option<i32> {
    let value = value?.as_i64()?;
    (1..=i64::from(MAX_INPUT))
        .contains(&value)
        .then_some(value as i32)
}

fn reservation_payload(value: &Value) -> Option<(Vec<(String, i32)>, i32)> {
    if value.as_object()?.len() != 2 {
        return None;
    }
    let ttl = number(value.get("ttl"))?;
    let values = value.get("items")?.as_array()?;
    if !(1..=100).contains(&values.len()) {
        return None;
    }
    let mut items = Vec::new();
    for item in values {
        if item.as_object()?.len() != 2 {
            return None;
        }
        let id = item.get("sku")?.as_str()?;
        if !sku(id) {
            return None;
        }
        items.push((id.to_owned(), number(item.get("quantity"))?));
    }
    items.sort_unstable();
    if items.windows(2).any(|pair| pair[0].0 == pair[1].0) {
        return None;
    }
    Some((items, ttl))
}

fn dispatch(request: &mut Request, url: &str, benchmark: bool) -> Result<Reply> {
    let path = request.url().split('?').next().unwrap_or("").to_owned();
    let parts: Vec<_> = path.split('/').skip(1).collect();
    let mut db = store::connect(url)?;
    if request.method() == &Method::Get {
        return match parts.as_slice() {
            ["healthz"] => {
                Ok(store::ready(&mut db).unwrap_or((503, json!({"status":"unavailable"}))))
            }
            ["v1", "stock", code] if sku(code) => store::stock(&mut db, code),
            ["v1", "stock", _] => Ok(error(400, "invalid_request")),
            ["v1", "reservations", id] => store::reservation(&mut db, id),
            _ => Ok(error(404, "not_found")),
        };
    }
    if request.method() != &Method::Post {
        return Ok(error(404, "not_found"));
    }
    let key = request
        .headers()
        .iter()
        .find(|h| h.field.equiv("Idempotency-Key"))
        .map(|h| h.value.to_string())
        .unwrap_or_default();
    if !identifier(&key, 128) {
        return Ok(error(400, "invalid_request"));
    }
    let is_json = request.headers().iter().any(|h| {
        h.field.equiv("Content-Type")
            && h.value
                .as_str()
                .split(';')
                .next()
                .is_some_and(|t| t.trim().eq_ignore_ascii_case("application/json"))
    });
    let mut bytes = Vec::new();
    request.as_reader().take(65537).read_to_end(&mut bytes)?;
    let body = if bytes.is_empty() {
        json!({})
    } else {
        if !is_json || bytes.len() > 65536 {
            return Ok(error(400, "invalid_request"));
        }
        match serde_json::from_slice(&bytes) {
            Ok(body) => body,
            Err(_) => return Ok(error(400, "invalid_request")),
        }
    };
    match parts.as_slice() {
        ["v1", "reservations"] => {
            let Some((items, ttl)) = reservation_payload(&body) else {
                return Ok(error(400, "invalid_request"));
            };
            let items_json: Vec<_> = items
                .iter()
                .map(|(sku, quantity)| json!({"sku":sku, "quantity":quantity}))
                .collect();
            let normalized = json!({"items":items_json, "ttl":ttl});
            store::write(&mut db, &key, &path, &normalized, |db, now| {
                store::reserve(db, now, &items, ttl)
            })
        }
        ["v1", "reservations", id, action @ ("confirm" | "cancel")] => {
            if body != json!({}) {
                return Ok(error(400, "invalid_request"));
            }
            let next = if *action == "confirm" {
                "confirmed"
            } else {
                "cancelled"
            };
            store::write(&mut db, &key, &path, &body, |db, _| {
                store::transition(db, id, next)
            })
        }
        ["v1", "clock", "advance"] if benchmark => {
            let Some(seconds) = number(body.get("seconds"))
                .filter(|_| body.as_object().is_some_and(|o| o.len() == 1))
            else {
                return Ok(error(400, "invalid_request"));
            };
            store::write(&mut db, &key, &path, &body, |db, now| {
                store::advance(db, now, seconds)
            })
        }
        _ => Ok(error(404, "not_found")),
    }
}

fn serve(args: ServerArguments) -> Result<()> {
    let server =
        Arc::new(Server::http((args.host.as_str(), args.port)).map_err(|err| err.to_string())?);
    std::thread::scope(|scope| {
        for _ in 0..4 {
            let server = Arc::clone(&server);
            let url = &args.database_url;
            let benchmark = args.benchmark_mode;
            scope.spawn(move || {
                for mut request in server.incoming_requests() {
                    let (status, body) =
                        dispatch(&mut request, url, benchmark).unwrap_or_else(|failure| {
                            eprintln!("{failure}");
                            error(500, "internal_error")
                        });
                    let result = request.respond(
                        Response::from_string(body.to_string())
                            .with_status_code(status)
                            .with_header(
                                Header::from_bytes("Content-Type", "application/json").unwrap(),
                            ),
                    );
                    if let Err(err) = result {
                        eprintln!("{err}");
                    }
                }
            });
        }
    });
    Ok(())
}

fn client(args: ClientArguments) -> Result<i32> {
    let encode = |value: &str| -> String {
        value
            .bytes()
            .map(|byte| {
                if byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.' | b'~') {
                    (byte as char).to_string()
                } else {
                    format!("%{byte:02X}")
                }
            })
            .collect()
    };
    let (path, mutation) = match args.command {
        ClientCommand::Health => ("/healthz".into(), None),
        ClientCommand::Stock { sku } => (format!("/v1/stock/{}", encode(&sku)), None),
        ClientCommand::Reservation { id } => (format!("/v1/reservations/{}", encode(&id)), None),
        ClientCommand::Confirm { id, request_id } => (
            format!("/v1/reservations/{}/confirm", encode(&id)),
            Some((request_id, json!({}))),
        ),
        ClientCommand::Cancel { id, request_id } => (
            format!("/v1/reservations/{}/cancel", encode(&id)),
            Some((request_id, json!({}))),
        ),
        ClientCommand::Reserve {
            item,
            ttl,
            request_id,
        } => {
            let items = item
                .iter()
                .map(|arg| -> Result<Value> {
                    let (sku, quantity) = arg.split_once('=').ok_or("expected SKU=QUANTITY")?;
                    Ok(json!({"sku": sku, "quantity": quantity.parse::<i32>()?}))
                })
                .collect::<Result<Vec<_>>>()?;
            (
                "/v1/reservations".into(),
                Some((request_id, json!({"items": items, "ttl": ttl}))),
            )
        }
        ClientCommand::Advance {
            seconds,
            request_id,
        } => (
            "/v1/clock/advance".into(),
            Some((request_id, json!({"seconds": seconds}))),
        ),
    };
    let url = format!("{}{path}", args.base_url.trim_end_matches('/'));
    let agent = ureq::AgentBuilder::new()
        .timeout(std::time::Duration::from_secs(10))
        .build();
    let response = if let Some((key, payload)) = mutation {
        agent
            .post(&url)
            .set("Idempotency-Key", &key)
            .set("Content-Type", "application/json")
            .send_string(&payload.to_string())
    } else {
        agent.get(&url).call()
    };
    match response {
        Ok(response) => {
            println!("{}", response.into_string()?);
            Ok(0)
        }
        Err(ureq::Error::Status(_, response)) => {
            println!("{}", response.into_string()?);
            Ok(1)
        }
        Err(err) => Err(err.into()),
    }
}

fn run() -> Result<i32> {
    let mut arguments: Vec<_> = std::env::args_os().collect();
    if arguments.get(1).is_some_and(|arg| arg == "client") {
        arguments.remove(1);
        return client(ClientArguments::parse_from(arguments));
    }
    let args = ServerArguments::parse_from(arguments);
    match args.command {
        ServerCommand::Migrate => store::migrate(&mut store::connect(&args.database_url)?)?,
        ServerCommand::Seed => {
            if !(0..=store::MAX_TIME).contains(&args.stock_a)
                || !(0..=store::MAX_TIME).contains(&args.stock_b)
            {
                return Err("initial stock must be between 0 and 2,000,000,000".into());
            }
            store::seed(
                &mut store::connect(&args.database_url)?,
                args.stock_a,
                args.stock_b,
            )?;
        }
        ServerCommand::Serve => serve(args)?,
    }
    Ok(0)
}

fn main() {
    match run() {
        Ok(status) => std::process::exit(status),
        Err(err) => {
            eprintln!("{err}");
            std::process::exit(2);
        }
    }
}
