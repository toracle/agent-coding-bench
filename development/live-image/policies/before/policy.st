discountRateFor: quantity
    ^ quantity >= 10 ifTrue: [10] ifFalse: [0]
