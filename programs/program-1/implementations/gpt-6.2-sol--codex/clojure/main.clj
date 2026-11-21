(ns orders)

(defonce orders (atom {}))

(defn add-order! [id quantity]
  {:pre [(integer? quantity) (<= 1 quantity 1000)]}
  (swap! orders assoc id quantity)
  quantity)

(defn discount-rate [quantity]
  (if (>= quantity 10) 10 0))

(defn quote-order [id]
  (let [quantity (or (get @orders id)
                     (throw (ex-info "Unknown order" {:id id})))]
    (quot (* quantity 1250 (- 100 (discount-rate quantity))) 100)))

(add-order! "A" 9)
(add-order! "B" 12)
(println "ready")
(flush)
(doseq [id (line-seq (java.io.BufferedReader. *in*))]
  (println (quote-order id))
  (flush))
