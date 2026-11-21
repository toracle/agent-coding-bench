| orders input output request quantity discount cents |
orders := Dictionary new.
orders at: #[65] put: 9.
orders at: #[66] put: 12.
input := Stdio stdin.
output := Stdio stdout.
output nextPutAll: 'ready' asByteArray; nextPut: 10; flush.

[request := input upTo: 10. request notEmpty] whileTrue: [
    quantity := orders at: request.
    discount := quantity >= 10 ifTrue: [10] ifFalse: [0].
    cents := (quantity * 1250 * (100 - discount)) // 100.
    output nextPutAll: cents printString asByteArray; nextPut: 10; flush
]!
