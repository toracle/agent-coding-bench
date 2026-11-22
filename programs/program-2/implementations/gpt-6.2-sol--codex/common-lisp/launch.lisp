(require :asdf)

(let ((setup (uiop:getenv "PROGRAM2_QUICKLISP_SETUP")))
  (when setup (load setup)))

(unless (find-package :ql)
  (error "Set PROGRAM2_QUICKLISP_SETUP to a Quicklisp setup.lisp"))
(funcall (find-symbol "QUICKLOAD" :ql) '(:dbd-sqlite3 :hunchentoot :yason :dexador :mito) :silent t)

(let* ((root (uiop:pathname-directory-pathname *load-truename*))
       (source (merge-pathnames "inventory.lisp" root))
       (file (uiop:getenv "PROGRAM2_LISP_FASL"))
       (args (uiop:command-line-arguments)))
  (cond
    ((equal (first args) "build")
     (unless file (error "Set PROGRAM2_LISP_FASL to the output FASL path"))
     (let ((profile (or (uiop:getenv "PROGRAM2_LISP_PROFILE") "debug")))
       (proclaim (ecase (intern (string-upcase profile) :keyword)
                   (:debug '(optimize (speed 0) (debug 3) (safety 3)))
                   (:optimized '(optimize (speed 3) (debug 1) (safety 3)))))
       (multiple-value-bind (output warnings failed) (compile-file source :output-file file)
         (declare (ignore output warnings))
         (uiop:quit (if failed 1 0)))))
    (t
     (load (or file source))
     (uiop:quit (funcall (find-symbol "MAIN" :inventory-bench) root args)))))
