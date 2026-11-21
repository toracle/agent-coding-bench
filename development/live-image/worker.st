| policyClass policy orders input output reply reload command quantity cents |
policyClass := (Object << #LiveQuotePolicy)
    package: 'AgentProgramBench-Live';
    install.
policy := policyClass new.
orders := Dictionary new.
input := Stdio stdin.
output := Stdio stdout.
reply := [:line | output nextPutAll: line asByteArray; nextPut: 10; flush].
reload := [policyClass compile: (FileSystem workingDirectory / 'policy.st') contents].

reload value.
reply value: 'ready'.
[command := input upTo: 10. command notEmpty] whileTrue: [
    command = 'seed' asByteArray
        ifTrue: [
            orders at: #[65] put: 9.
            orders at: #[66] put: 12.
            reply value: 'seeded']
        ifFalse: [
            command = 'reload' asByteArray
                ifTrue: [reload value. reply value: 'reloaded']
                ifFalse: [
                    quantity := orders at: command.
                    cents := (quantity * 1250 * (100 - (policy discountRateFor: quantity))) // 100.
                    reply value: cents printString]]
]!
