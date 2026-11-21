# Program 1 · Order quotes

Build a dependency-free, persistent line-based worker. Store two orders in
memory: A has 9 units and B has 12 units. Each unit costs 1,250 cents.
Orders with at least 10 units receive 10% off the entire order. Use integer
arithmetic and discard any fractional cent.

Emit `ready` and flush after initialization. For each input line containing
`A` or `B`, emit the integer quote in cents followed by a newline and flush.
Exit successfully at EOF. Standard output is reserved for protocol messages.
Other inputs are outside this workload's contract.

Implementations may vary by language or toolchain. Benchmark workers receive
requests one at a time on standard input and respond on standard output.
