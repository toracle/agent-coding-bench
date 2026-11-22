(defpackage :inventory-bench (:use :cl))
(in-package :inventory-bench)

(mito:deftable stock ()
  ((sku :col-type (:varchar 32)) (available :col-type :integer))
  (:primary-key sku) (:record-timestamps nil))
(mito:deftable clock ()
  ((id :col-type :integer) (now :col-type :integer))
  (:primary-key id) (:record-timestamps nil))
(mito:deftable reservation ()
  ((id :col-type (:varchar 36)) (status :col-type (:varchar 12)) (expires-at :col-type :integer))
  (:table-name "reservations") (:primary-key id) (:record-timestamps nil))
(mito:deftable reservation-item ()
  ((reservation-id :col-type (:varchar 36)) (sku :col-type (:varchar 32)) (quantity :col-type :integer))
  (:table-name "reservation_items") (:primary-key reservation-id sku) (:record-timestamps nil))
(mito:deftable recorded-response ()
  ((key :col-type (:varchar 256)) (fingerprint :col-type :text)
   (status :col-type :integer) (body :col-type :text))
  (:table-name "recorded_responses") (:primary-key key) (:record-timestamps nil))

(defconstant +limit+ 2000000000)
(defvar *database-url*)
(defvar *benchmark-mode*)
(defvar *root*)
(defvar *writers* (bt:make-lock "inventory writers"))

(defun object (&rest entries)
  (let ((result (make-hash-table :test 'equal)))
    (loop for (key value) on entries by #'cddr do (setf (gethash key result) value))
    result))

(defun failure (status code)
  (values status (object "error" (object "code" code))))

(defun invalid () (failure 400 "invalid_request"))

(defun sql (statement &optional parameters)
  (dbi:do-sql mito:*connection* statement parameters))

;; ECL 24.5.10 with the pinned CL-DBI build double-steps the SQLite
;; statement in the stock fetch method. Keep the fix on our connection class.
#+ecl
(progn
  (defclass program2-sqlite-connection (dbd.sqlite3:dbd-sqlite3-connection) ())
  (defmethod dbi:connection-driver-type ((connection program2-sqlite-connection))
    (declare (ignore connection))
    :sqlite3)

  (defmethod dbi.driver:fetch-using-connection ((connection program2-sqlite-connection)
                                                (query dbd.sqlite3:dbd-sqlite3-query))
    (declare (ignore connection) (optimize (speed 0) (safety 3) (debug 3)))
    (if (slot-boundp query 'dbi.driver::results)
        (pop (dbi.driver:query-results query))
        (let ((statement (dbi.driver:query-prepared query)))
          (handler-bind ((sqlite:sqlite-error
                           (lambda (failure)
                             (declare (ignore failure))
                             (sqlite:finalize-statement statement)
                             (return-from dbi.driver:fetch-using-connection nil))))
            (when (sqlite:step-statement statement)
              (let ((fields (if (slot-boundp query 'dbi.driver::fields)
                                (dbi.driver:query-fields query)
                                (setf (dbi.driver:query-fields query)
                                      (sqlite:statement-column-names statement)))))
                (loop for index from 0 below (length fields)
                      collect (sqlite:statement-column-value statement index)))))))))

(defun connect-sqlite (database)
  (let ((connection (dbi:connect :sqlite3 :database-name database)))
    #+ecl (change-class connection 'program2-sqlite-connection)
    #-ecl connection))

(defmacro with-db (&body body)
  `(let ((mito:*connection*
           (progn
             (unless (uiop:string-prefix-p "sqlite:///" *database-url*)
               (error "This build supports sqlite:/// database URLs"))
             (connect-sqlite (subseq *database-url* 10)))))
     (unwind-protect
          (progn (sql "PRAGMA foreign_keys = ON")
                 (sql "PRAGMA busy_timeout = 10000")
                 ,@body)
       (dbi:disconnect mito:*connection*))))

(defun migrate ()
  (with-db
    (dbi:with-transaction mito:*connection*
      (dolist (statement (uiop:split-string
                          (uiop:read-file-string (merge-pathnames "migration-0001.sql" *root*))
                          :separator '(#\;)))
        (when (find-if-not (lambda (c) (find c '(#\Space #\Tab #\Return #\Newline))) statement)
          (sql statement))))))

(defun seed (a b)
  (with-db
    (dbi:with-transaction mito:*connection*
      (loop for sku in '("A" "B") for count in (list a b)
            unless (mito:find-dao 'stock :sku sku)
              do (mito:create-dao 'stock :sku sku :available count)))))

(defun item-object (sku quantity)
  (object "sku" sku "quantity" quantity))

(defun items (id)
  (sort (mito:retrieve-dao 'reservation-item :reservation-id id)
        #'string< :key #'reservation-item-sku))

(defun representation (row &optional (line-items (items (reservation-id row))))
  (object "id" (reservation-id row) "status" (reservation-status row)
          "expires_at" (reservation-expires-at row)
          "items" (map 'vector (lambda (item) (item-object (reservation-item-sku item)
                                                         (reservation-item-quantity item)))
                       line-items)))

(defun json (value)
  (with-output-to-string (out) (yason:encode value out)))

(defun parse-json (data)
  (let ((yason:*parse-json-booleans-as-symbols* t))
    (yason:parse data)))

(defun write-request (key path normalized operation)
  (let ((fingerprint (json (vector "POST" path normalized)))
        (encoded-key (with-output-to-string (out)
                       (loop for c across key do (format out "~2,'0X" (char-code c))))))
    (bt:with-lock-held (*writers*)
      (dbi:with-transaction mito:*connection*
        (sql "UPDATE clock SET now = now WHERE id = 1")
        (let ((prior (mito:find-dao 'recorded-response :key encoded-key)))
          (if prior
              (if (string= fingerprint (recorded-response-fingerprint prior))
                  (values (recorded-response-status prior) (parse-json (recorded-response-body prior)))
                  (failure 409 "idempotency_conflict"))
              (multiple-value-bind (status body) (funcall operation (mito:find-dao 'clock :id 1))
                (unless (= status 400)
                  (mito:create-dao 'recorded-response :key encoded-key :fingerprint fingerprint
                                   :status status :body (json body)))
                (values status body))))))))

(defun reserve (clock lines ttl)
  (let* ((expires (+ (clock-now clock) ttl))
         (stocks (mapcar (lambda (line) (mito:find-dao 'stock :sku (car line))) lines)))
    (cond
      ((> expires +limit+) (invalid))
      ((some #'null stocks) (failure 404 "unknown_sku"))
      ((some (lambda (stock line) (< (stock-available stock) (cdr line))) stocks lines)
       (failure 409 "insufficient_stock"))
      (t
       (let* ((id (string-downcase (princ-to-string (uuid:make-v4-uuid))))
              (row (mito:create-dao 'reservation :id id :status "pending" :expires-at expires))
              (items (loop for stock in stocks for (sku . count) in lines
                           do (decf (stock-available stock) count) (mito:save-dao stock)
                           collect (mito:create-dao 'reservation-item :reservation-id id
                                                    :sku sku :quantity count))))
         (values 201 (representation row items)))))))

(defun release-stock (line-items)
  (dolist (item line-items)
    (let ((stock (mito:find-dao 'stock :sku (reservation-item-sku item))))
      (incf (stock-available stock) (reservation-item-quantity item))
      (mito:save-dao stock))))

(defun change-reservation (id status)
  (let ((row (mito:find-dao 'reservation :id id)))
    (cond
      ((not row) (failure 404 "unknown_reservation"))
      ((not (string= "pending" (reservation-status row))) (failure 409 "invalid_transition"))
      (t
       (let ((line-items (items id)))
         (unless (string= "confirmed" status) (release-stock line-items))
         (setf (reservation-status row) status)
         (mito:save-dao row)
         (values 200 (representation row line-items)))))))

(defun advance-clock (clock seconds)
  (let ((now (+ (clock-now clock) seconds)))
    (when (> now +limit+) (return-from advance-clock (invalid)))
    ;; Group while the reservations are still pending. The caller holds the write
    ;; transaction across stock restoration, state changes, and the replay record.
    (dolist (total (mito:retrieve-by-sql
                    "SELECT i.sku, SUM(i.quantity)
                     FROM reservation_items i
                     JOIN reservations r ON r.id = i.reservation_id
                     WHERE r.status = ? AND r.expires_at <= ?
                     GROUP BY i.sku"
                    :binds (list "pending" now) :format :values))
      (destructuring-bind (sku quantity) total
        (sql "UPDATE stock SET available = available + ? WHERE sku = ?"
             (list quantity sku))))
    (let ((expired (sql "UPDATE reservations SET status = ? WHERE status = ? AND expires_at <= ?"
                        (list "expired" "pending" now))))
      (setf (clock-now clock) now)
      (mito:save-dao clock)
      (values 200 (object "now" now "expired" expired)))))

(defun identifier-p (text limit lowercase)
  (and (stringp text) (<= 1 (length text) limit)
       (every (lambda (c)
                (or (char<= #\A c #\Z) (char<= #\0 c #\9)
                    (and lowercase (char<= #\a c #\z)) (find c "_-")))
              text)))

(defun positive-p (value)
  (and (integerp value) (<= 1 value 1000000)))

(defun fields-p (object &rest fields)
  (and (hash-table-p object) (= (hash-table-count object) (length fields))
       (every (lambda (key) (nth-value 1 (gethash key object))) fields)))

(defun reservation-input (payload)
  (when (fields-p payload "ttl" "items")
    (let ((ttl (gethash "ttl" payload)) (items (gethash "items" payload)))
      (when (and (positive-p ttl) (listp items) (<= 1 (length items) 100))
        (let ((seen (make-hash-table :test 'equal)) (lines nil))
          (dolist (item items)
            (unless (fields-p item "sku" "quantity") (return-from reservation-input nil))
            (let ((sku (gethash "sku" item)) (count (gethash "quantity" item)))
              (unless (and (identifier-p sku 32 nil) (positive-p count) (not (gethash sku seen)))
                (return-from reservation-input nil))
              (setf (gethash sku seen) t)
              (push (cons sku count) lines)))
          (values (sort lines #'string< :key #'car) ttl))))))

(defun request-body ()
  (let ((raw (hunchentoot:raw-post-data :force-text t)))
    (cond
      ((or (null raw) (zerop (length raw))) (object))
      ((and (<= (length raw) 65536)
            (string-equal "application/json" (first (uiop:split-string
              (or (hunchentoot:header-in* :content-type) "") :separator '(#\;)))))
       (handler-case (parse-json raw) (error () nil))))))

(defun dispatch (method path)
  (let* ((parts (rest (uiop:split-string path :separator '(#\/))))
         (id (third parts)))
    (case method
      (:get
       (cond
         ((string= path "/healthz")
          (if (mito:find-dao 'clock :id 1)
              (values 200 (object "status" "ready"))
              (values 503 (object "status" "unavailable"))))
         ((and (= (length parts) 3) (equal (subseq parts 0 2) '("v1" "stock")))
          (if (identifier-p id 32 nil)
              (let ((row (mito:find-dao 'stock :sku id)))
                (if row (values 200 (object "sku" id "available" (stock-available row)))
                    (failure 404 "unknown_sku")))
              (invalid)))
         ((and (= (length parts) 3) (equal (subseq parts 0 2) '("v1" "reservations")))
          (let ((row (mito:find-dao 'reservation :id id)))
            (if row (values 200 (representation row)) (failure 404 "unknown_reservation"))))
         (t (failure 404 "not_found"))))
      (:post
       (let ((key (hunchentoot:header-in* :idempotency-key)) (body (request-body)))
         (unless (identifier-p key 128 t) (return-from dispatch (invalid)))
         (cond
           ((string= path "/v1/reservations")
            (multiple-value-bind (lines ttl) (reservation-input body)
              (if lines
                  (write-request key path
                    (object "ttl" ttl "items" (map 'vector (lambda (line) (item-object (car line) (cdr line))) lines))
                    (lambda (clock) (reserve clock lines ttl)))
                  (invalid))))
           ((and (= (length parts) 4) (equal (subseq parts 0 2) '("v1" "reservations"))
                 (member (fourth parts) '("confirm" "cancel") :test #'equal))
            (if (fields-p body)
                (write-request key path body
                  (lambda (clock) (declare (ignore clock))
                    (change-reservation id (if (string= "confirm" (fourth parts)) "confirmed" "cancelled"))))
                (invalid)))
           ((and *benchmark-mode* (string= path "/v1/clock/advance"))
            (if (and (fields-p body "seconds") (positive-p (gethash "seconds" body)))
                (write-request key path body (lambda (clock) (advance-clock clock (gethash "seconds" body))))
                (invalid)))
           (t (failure 404 "not_found")))))
      (t (failure 404 "not_found")))))

(defun handle-http ()
  (let ((path (hunchentoot:script-name*)))
    (multiple-value-bind (status body)
        (handler-case
            (with-db (dispatch (hunchentoot:request-method*) path))
          (error (cause)
            (format *error-output* "~&~A: ~A~%" path cause)
            (if (string= path "/healthz")
                (values 503 (object "status" "unavailable"))
                (failure 500 "internal_error"))))
      (setf (hunchentoot:return-code*) status
            (hunchentoot:content-type*) "application/json")
      (json body))))

(defun arguments (args)
  (let ((options nil) (positional nil))
    (loop while args for arg = (pop args)
          do (cond
               ((string= arg "--benchmark-mode") (push (cons arg t) options))
               ((uiop:string-prefix-p "--" arg)
                (unless args (error "Missing value for ~A" arg))
                (push (cons arg (pop args)) options))
               (t (push arg positional))))
    (values (nreverse positional) (nreverse options))))

(defun opt (options key &optional default)
  (or (cdr (assoc key options :test #'equal)) default))

(defun client (args)
  (multiple-value-bind (pos options) (arguments args)
    (destructuring-bind (verb &optional target) pos
      (let* ((base (string-right-trim "/" (opt options "--base-url"
                    (or (uiop:getenv "BASE_URL") "http://127.0.0.1:8080"))))
             (encoded (and target (quri:url-encode target)))
             (body nil)
             (path (cond
                     ((equal verb "health") "/healthz")
                     ((equal verb "stock") (concatenate 'string "/v1/stock/" encoded))
                     ((equal verb "reservation") (concatenate 'string "/v1/reservations/" encoded))
                     ((member verb '("confirm" "cancel") :test #'equal)
                      (setf body (object))
                      (format nil "/v1/reservations/~A/~A" encoded verb))
                     ((equal verb "advance")
                      (setf body (object "seconds" (parse-integer target)))
                      "/v1/clock/advance")
                     ((equal verb "reserve")
                      (let ((items (loop for (option . value) in options when (equal option "--item")
                                         collect (destructuring-bind (sku count)
                                                     (uiop:split-string value :separator '(#\=))
                                                   (item-object sku (parse-integer count))))))
                        (unless items (error "Expected --item SKU=QUANTITY"))
                        (setf body (object "items" (coerce items 'vector) "ttl"
                                           (parse-integer (opt options "--ttl")))))
                      "/v1/reservations")
                     (t (error "Unknown client command")))))
        (multiple-value-bind (response status)
            (handler-case
                (if body
                    (dex:post (concatenate 'string base path)
                          :headers (list (cons "Content-Type" "application/json")
                                         (cons "Idempotency-Key" (or (opt options "--request-id")
                                                                    (error "Missing --request-id"))))
                          :content (json body) :force-string t)
                    (dex:get (concatenate 'string base path) :force-string t))
              (dexador.error:http-request-failed (condition)
                (values (dexador.error:response-body condition)
                        (dexador.error:response-status condition))))
          (write-line response)
          (if (<= 200 status 299) 0 1))))))

(defun main (root args)
  (handler-case
      (if (equal (first args) "client") (client (rest args))
          (multiple-value-bind (pos options) (arguments args)
            (destructuring-bind (action) pos
              (setf *root* root
                    *database-url* (opt options "--database-url"
                                        (or (uiop:getenv "DATABASE_URL") "sqlite:///inventory.db"))
                    *benchmark-mode* (opt options "--benchmark-mode"))
              (progn
                (cond
                  ((equal action "migrate") (migrate))
                  ((equal action "seed")
                   (let ((a (parse-integer (opt options "--stock-a" "10")))
                         (b (parse-integer (opt options "--stock-b" "5"))))
                     (unless (and (<= 0 a +limit+) (<= 0 b +limit+)) (error "Invalid initial stock"))
                     (seed a b)))
                  ((equal action "serve")
                   (setf hunchentoot:*dispatch-table*
                         (list (hunchentoot:create-prefix-dispatcher "/" #'handle-http)))
                   (let ((hunchentoot:*show-lisp-errors-p* nil))
                     (hunchentoot:start (make-instance 'hunchentoot:easy-acceptor
                                        :address (opt options "--host" "127.0.0.1")
                                        :port (parse-integer (opt options "--port" "8080"))
                                        :access-log-destination nil))
                     (loop (sleep 60))))
                  (t (error "Expected migrate, seed, serve or client"))))
              0)))
    (error (cause)
      (format *error-output* "~&~A~%" cause)
      2)))
