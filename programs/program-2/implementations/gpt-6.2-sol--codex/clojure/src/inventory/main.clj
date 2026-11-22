(ns inventory.main
  (:require [clojure.data.json :as json]
            [clojure.string :as str]
            [inventory.store :as db])
  (:import [com.sun.net.httpserver HttpHandler HttpServer]
           [java.net InetSocketAddress URI URLEncoder]
           [java.net.http HttpClient HttpRequest HttpRequest$BodyPublishers HttpResponse$BodyHandlers]
           [java.nio.charset StandardCharsets]
           [java.time Duration]
           [java.util.concurrent Executors]))

(defn positive? [x] (and (integer? x) (<= 1 x 1000000)))
(defn sku? [x] (and (string? x) (re-matches #"[A-Z0-9_-]{1,32}" x)))
(defn key? [x] (and (string? x) (re-matches #"[A-Za-z0-9_-]{1,128}" x)))
(def invalid [400 {:error {:code "invalid_request"}}])
(def missing [404 {:error {:code "not_found"}}])

(defn payload [exchange]
  (let [data (.readNBytes (.getRequestBody exchange) 65537)
        content (.getFirst (.getRequestHeaders exchange) "Content-Type")]
    (cond
      (zero? (alength data)) {}
      (and (<= (alength data) 65536)
           (some-> content str/lower-case (str/split #";") first str/trim (= "application/json")))
      (try (json/read-str (String. data StandardCharsets/UTF_8) :key-fn keyword)
           (catch Exception _ ::invalid))
      :else ::invalid)))

(defn reserve-body [body]
  (let [{:keys [items ttl]} (when (map? body) body)]
    (when (and (map? body) (= (set (keys body)) #{:items :ttl})
               (positive? ttl) (vector? items) (<= 1 (count items) 100)
               (every? #(and (map? %) (= #{:sku :quantity} (set (keys %)))
                             (sku? (:sku %)) (positive? (:quantity %))) items)
               (= (count items) (count (set (map :sku items)))))
      {:items (mapv #(array-map :sku (:sku %) :quantity (:quantity %)) (sort-by :sku items)) :ttl ttl})))

(defn dispatch [exchange url benchmark?]
  (let [path (.getPath (.getRequestURI exchange))
        method (.getRequestMethod exchange)
        parts (str/split (subs path 1) #"/" -1)
        [v1 resource id action] parts
        length (count parts)]
    (if (= "GET" method)
      (if (= path "/healthz")
        (try (db/with-db [connection url] (db/ready))
             (catch Exception _ [503 {:status "unavailable"}]))
        (db/with-db [connection url]
          (cond
            (and (= v1 "v1") (= resource "stock") (= 3 length))
            (if (sku? id) (db/stock id) invalid)

            (and (= v1 "v1") (= resource "reservations") (= 3 length))
            (db/reservation id)

            :else missing)))
      (if (not= method "POST")
        missing
        (let [key (.getFirst (.getRequestHeaders exchange) "Idempotency-Key")
              body (payload exchange)]
          (if-not (key? key)
            invalid
            (db/with-db [connection url]
              (cond
                (= path "/v1/reservations")
                (if-let [normalized (reserve-body body)]
                  (db/write! connection key path normalized #(db/reserve % normalized))
                  invalid)

                (and (= v1 "v1") (= resource "reservations") (= 4 length)
                     (contains? #{"cancel" "confirm"} action))
                (if (= body {})
                  (db/write! connection key path {} (fn [_] (db/change id
                    (if (= action "cancel") "cancelled" "confirmed"))))
                  invalid)

                (and benchmark? (= path "/v1/clock/advance"))
                (if (and (map? body) (= #{:seconds} (set (keys body))) (positive? (:seconds body)))
                  (db/write! connection key path body #(db/advance! % (:seconds body)))
                  invalid)

                :else missing))))))))

(defn serve [url host port benchmark?]
  (System/setProperty "sun.net.httpserver.nodelay" "true")
  (let [server (HttpServer/create (InetSocketAddress. ^String host (int port)) 0)]
    (.createContext server "/"
      (reify HttpHandler
        (handle [_ exchange]
          (let [[status body] (try (dispatch exchange url benchmark?)
                                   (catch Exception e
                                     (binding [*out* *err*] (println (.getMessage e)))
                                     [500 {:error {:code "internal_error"}}]))
                bytes (.getBytes (json/write-str body) StandardCharsets/UTF_8)]
            (.set (.getResponseHeaders exchange) "Content-Type" "application/json")
            (.sendResponseHeaders exchange status (alength bytes))
            (with-open [out (.getResponseBody exchange)]
              (.write out bytes))))))
    (.setExecutor server (Executors/newFixedThreadPool 4))
    (.start server)))

(defn options [args switches]
  (loop [args args result {:positional []}]
    (if-let [arg (first args)]
      (cond
        (contains? switches arg) (recur (next args) (assoc result arg true))
        (str/starts-with? arg "--")
        (let [value (second args)]
          (when-not value (throw (ex-info (str "missing value for " arg) {})))
          (recur (nnext args)
                 (if (= arg "--item") (update result arg (fnil conj []) value) (assoc result arg value))))
        :else (recur (next args) (update result :positional conj arg)))
      result)))

(defn encode [text]
  (str/replace (URLEncoder/encode ^String text StandardCharsets/UTF_8) "+" "%20"))

(defn client [args]
  (let [{:strs [--base-url --item --ttl --request-id] :keys [positional]} (options args #{})
        [verb target] positional
        id #(encode (or target (throw (ex-info "missing target" {}))))
        [path body] (case verb
                     "health" ["/healthz" nil]
                     "stock" [(str "/v1/stock/" (id)) nil]
                     "reservation" [(str "/v1/reservations/" (id)) nil]
                     ("confirm" "cancel") [(str "/v1/reservations/" (id) "/" verb) {}]
                     "reserve" ["/v1/reservations"
                                {:items (mapv (fn [item] (let [[sku quantity] (str/split item #"=" 2)]
                                    {:sku sku :quantity (parse-long quantity)})) --item)
                                 :ttl (parse-long --ttl)}]
                     "advance" ["/v1/clock/advance" {:seconds (parse-long target)}]
                     (throw (ex-info "unknown client command" {})))
        base (or --base-url (System/getenv "BASE_URL") "http://127.0.0.1:8080")
        request (HttpRequest/newBuilder (URI/create (str (str/replace base #"/+$" "") path)))]
    (.timeout request (Duration/ofSeconds 10))
    (if body
      (do
        (when-not --request-id (throw (ex-info "missing --request-id" {})))
        (.header request "Idempotency-Key" --request-id)
        (.header request "Content-Type" "application/json")
        (.POST request (HttpRequest$BodyPublishers/ofString (json/write-str body))))
      (.GET request))
    (let [response (.send (HttpClient/newHttpClient) (.build request) (HttpResponse$BodyHandlers/ofString))]
      (println (str/trim-newline (.body response)))
      (if (<= 200 (.statusCode response) 299) 0 1))))

(defn -main [& args]
  (try
    (if (= (first args) "client")
      (System/exit (client (rest args)))
      (let [{:strs [--database-url --port --host --benchmark-mode --stock-a --stock-b]
             :keys [positional]} (options args #{"--benchmark-mode"})
            url (or --database-url (System/getenv "DATABASE_URL") "sqlite:///inventory.db")
            a (parse-long (or --stock-a "10"))
            b (parse-long (or --stock-b "5"))]
        (case (first positional)
          "migrate" (db/migrate url)
          "seed" (if (and a b (<= 0 a db/max-time) (<= 0 b db/max-time))
                   (db/seed url a b)
                   (throw (ex-info "invalid initial stock" {})))
          "serve" (serve url (or --host "127.0.0.1") (parse-long (or --port "8080")) --benchmark-mode)
          (throw (ex-info "expected migrate, seed, serve or client" {})))))
    (catch Exception e
      (binding [*out* *err*] (println (.getMessage e)))
      (System/exit 2))))
