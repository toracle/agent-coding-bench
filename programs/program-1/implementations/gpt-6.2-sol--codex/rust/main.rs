use std::collections::HashMap;

fn add_order(
    orders: &mut HashMap<String, u32>,
    id: &str,
    quantity: u32,
) -> Result<u32, &'static str> {
    if !(1..=1000).contains(&quantity) {
        return Err("quantity must be an integer from 1 to 1000");
    }
    orders.insert(id.to_owned(), quantity);
    Ok(quantity)
}

fn discount_rate(quantity: u32) -> u32 {
    if quantity >= 10 {
        10
    } else {
        0
    }
}

fn quote_order(orders: &HashMap<String, u32>, id: &str, discount: fn(u32) -> u32) -> Option<u32> {
    let quantity = *orders.get(id)?;
    Some(quantity * 1250 * (100 - discount(quantity)) / 100)
}

fn main() -> std::io::Result<()> {
    use std::io::{self, BufRead, Write};

    let mut orders = HashMap::new();
    add_order(&mut orders, "A", 9).unwrap();
    add_order(&mut orders, "B", 12).unwrap();

    let mut input = io::stdin().lock();
    let mut output = io::stdout().lock();
    let mut line = String::new();
    writeln!(output, "ready")?;
    output.flush()?;
    while input.read_line(&mut line)? != 0 {
        let quote = quote_order(&orders, line.trim_end_matches('\n'), discount_rate).unwrap();
        writeln!(output, "{quote}")?;
        output.flush()?;
        line.clear();
    }
    Ok(())
}
