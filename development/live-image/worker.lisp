(defvar *orders* (make-hash-table :test 'equal))

(defun reply (value)
  (format t "~A~%" value)
  (finish-output))

(defun reload-policy ()
  (load "policy.lisp" :verbose nil :print nil))

(defun quote-order (id)
  (let* ((quantity (gethash id *orders*))
         (discount (funcall (symbol-function 'discount-rate) quantity)))
    (floor (* quantity 1250 (- 100 discount)) 100)))

(reload-policy)
(reply "ready")
(loop for command = (read-line *standard-input* nil)
      while command
      do (cond ((string= command "seed")
                (setf (gethash "A" *orders*) 9
                      (gethash "B" *orders*) 12)
                (reply "seeded"))
               ((string= command "reload")
                (reload-policy)
                (reply "reloaded"))
               (t (reply (quote-order command)))))
