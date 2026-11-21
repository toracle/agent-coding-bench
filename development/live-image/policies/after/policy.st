discountRateFor: quantity
    ^ quantity >= 8 ifTrue: [15] ifFalse: [0]
