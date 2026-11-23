(ns inventory.store
  (:require [clojure.data.json :as json]
            [clojure.java.io :as io]
            [clojure.string :as str]
            [next.jdbc :as jdbc]
            [next.jdbc.result-set :as rs]
            [toucan2.core :as t]
            [methodical.core :as m])
  (:import [java.util UUID]))

(def max-time 2000000000)

(m/defmethod t/primary-keys ::stock [_] [:sku])
(m/defmethod t/primary-keys ::recorded_responses [_] [:key])
(m/defmethod t/primary-keys ::reservation_items [_] [:reservation_id :sku])

(defn error [status code] [status {:error {:code code}}])

(defn databank [url]
  (when-not (str/starts-with? url "sqlite:///")
    (throw (ex-info "This submission currently supports sqlite:/// URLs" {})))
  {:jdbcUrl (str "jdbc:sqlite:" (subs url 10))})

(defmacro with-db [[db url] & body]
  `(with-open [~db (jdbc/get-connection (databank ~url))]
     (jdbc/execute! ~db ["PRAGMA foreign_keys = ON"])
     (jdbc/execute! ~db ["PRAGMA busy_timeout = 10000"])
     (t/with-connection [connection# ~db]
       ~@body)))

(defn migrate [url]
  (with-db [db url]
    (t/with-transaction [tx db]
      (doseq [sql (remove str/blank? (str/split (slurp (io/resource "0001_inventory.sql")) #";"))]
        (jdbc/execute! tx [sql])))))

(defn seed [url a b]
  (with-db [db url]
    (t/with-transaction [_ db]
      (doseq [[sku available] [["A" a] ["B" b]]]
        (when-not (t/select-one ::stock :sku sku)
          (t/insert! ::stock {:sku sku :available available}))))))

(defn ready []
  (if (t/select-one ::clock :id 1)
    [200 {:status "ready"}]
    [503 {:status "unavailable"}]))

(defn stock [sku]
  (if-let [row (t/select-one ::stock :sku sku)]
    [200 (select-keys row [:sku :available])]
    (error 404 "unknown_sku")))

(defn items [id]
  (->> (t/select ::reservation_items :reservation_id id)
       (map #(select-keys % [:sku :quantity]))
       (sort-by :sku)
       vec))

(defn representation [row]
  (assoc (select-keys row [:id :status :expires_at]) :items (items (:id row))))

(defn reservation [id]
  (if-let [row (t/select-one ::reservations :id id)]
    [200 (representation row)]
    (error 404 "unknown_reservation")))

(defn write! [db key path payload operation]
  (let [key (apply str (map #(format "%02x" (int %)) key))
        fingerprint (json/write-str ["POST" path payload])]
    (t/with-transaction [tx db]
      ;; Acquire a write lock before reading state.
      (jdbc/execute! tx ["UPDATE clock SET now = now WHERE id = 1"])
      (if-let [previous (t/select-one ::recorded_responses :key key)]
        (if (= fingerprint (:fingerprint previous))
          [(:status previous) (json/read-str (:body previous) :key-fn keyword)]
          (error 409 "idempotency_conflict"))
        (let [[status body :as response] (operation tx (:now (t/select-one ::clock :id 1)))]
          (when-not (= status 400)
            (t/insert! ::recorded_responses {:key key :fingerprint fingerprint
                                            :status status :body (json/write-str body)}))
          response)))))

(defn adjust! [sku delta]
  (let [available (:available (t/select-one ::stock :sku sku))]
    (t/update! ::stock :sku sku {:available (+ available delta)})))

(defn reserve [now {:keys [items ttl]}]
  (let [expires (+ now ttl)
        stocks (mapv #(t/select-one ::stock :sku (:sku %)) items)]
    (cond
      (> expires max-time) (error 400 "invalid_request")
      (some nil? stocks) (error 404 "unknown_sku")
      (some true? (map #(< (:available %1) (:quantity %2)) stocks items)) (error 409 "insufficient_stock")
      :else
      (let [id (str (UUID/randomUUID))
            row {:id id :status "pending" :expires_at expires}]
        (t/insert! ::reservations row)
        (doseq [{:keys [sku quantity] :as item} items]
          (adjust! sku (- quantity))
          (t/insert! ::reservation_items (assoc item :reservation_id id)))
        [201 (assoc row :items items)]))))

(defn change [id status]
  (if-let [row (t/select-one ::reservations :id id)]
    (if (= "pending" (:status row))
      (let [line-items (items id)]
        (when (not= "confirmed" status)
          (doseq [{:keys [sku quantity]} line-items]
            (adjust! sku quantity)))
        (t/update! ::reservations :id id {:status status})
        [200 (assoc (select-keys row [:id :expires_at]) :status status :items line-items)])
      (error 409 "invalid_transition"))
    (error 404 "unknown_reservation")))

(defn advance! [connection now seconds]
  (let [new-time (+ now seconds)]
    (if (> new-time max-time)
      (error 400 "invalid_request")
      (let [totals (jdbc/execute! connection
                     ["SELECT i.sku, SUM(i.quantity) AS quantity
                       FROM reservation_items i JOIN reservations r ON r.id = i.reservation_id
                       WHERE r.status = ? AND r.expires_at <= ? GROUP BY i.sku"
                      "pending" new-time]
                     {:builder-fn rs/as-unqualified-lower-maps})]
        (doseq [{:keys [sku quantity]} totals]
          (jdbc/execute-one! connection
            ["UPDATE stock SET available = available + ? WHERE sku = ?" quantity sku]))
        (let [expired (:next.jdbc/update-count
                        (jdbc/execute-one! connection
                          ["UPDATE reservations SET status = ? WHERE status = ? AND expires_at <= ?"
                           "expired" "pending" new-time]))]
          (t/update! ::clock :id 1 {:now new-time})
          [200 {:now new-time :expired expired}])))))
