Eval [
    | orders writeLine |
    orders := Dictionary new.
    orders at: 'A' put: 9.
    orders at: 'B' put: 12.

    writeLine := [:line |
        stdout nextPutAll: line; nextPut: (Character value: 10); flush
    ].
    writeLine value: 'ready'.

    [stdin atEnd] whileFalse: [
        | quantity discount cents |
        quantity := orders at: stdin nextLine.
        discount := quantity >= 10 ifTrue: [10] ifFalse: [0].
        cents := (quantity * 1250 * (100 - discount)) // 100.
        writeLine value: cents printString
    ]
]
