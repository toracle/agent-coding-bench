(defvar *orders* (make-hash-table :test 'equal))

(defun add-order (id quantity)
  (check-type quantity (integer 1 1000))
  (setf (gethash id *orders*) quantity))

(defun discount-rate (quantity)
  (if (>= quantity 10) 10 0))

(defun quote-order (id)
  (let ((quantity (or (gethash id *orders*) (error "Unknown order: ~A" id))))
    (values (floor (* quantity 1250 (- 100 (discount-rate quantity))) 100))))

(defun main ()
  (add-order "A" 9)
  (add-order "B" 12)
  (write-line "ready")
  (finish-output)
  (loop for id = (read-line *standard-input* nil)
        while id
        do (format t "~D~%" (quote-order id))
           (finish-output)))
