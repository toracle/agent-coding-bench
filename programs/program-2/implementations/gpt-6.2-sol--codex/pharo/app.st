| descriptors package |
package := 'AgentProgramBench-Inventory'.
descriptors := (DescriptorSystem << #BenchInventoryDescriptors) package: package; install.
#(
  (BenchStock stock ((sku sku text primary) (available available integer)))
  (BenchClock clock ((id id integer primary) (now now integer)))
  (BenchReservation reservations ((id id text primary) (status status text) (expires expires_at integer)))
  (BenchItem reservation_items ((reservationId reservation_id text primary) (sku sku text primary) (quantity quantity integer)))
  (BenchResponse recorded_responses ((key key text primary) (fingerprint fingerprint text) (status status integer) (body body text)))
) do: [ :definition |
    | model modelName tableName fields |
    modelName := definition first.
    tableName := definition second asUppercase.
    fields := definition third.
    model := (Object << modelName) slots: (fields collect: #first); package: package; install.
    fields do: [ :field |
        model compile: field first, ' ^ ', field first.
        model compile: field first, ': value ', field first, ' := value' ].
    descriptors compile: (String streamContents: [ :s |
        s nextPutAll: 'tableFor'; nextPutAll: tableName; nextPutAll: ': table '.
        fields do: [ :field |
            s nextPutAll: '(table createFieldNamed: '; print: field second asString;
                nextPutAll: ' type: '; nextPutAll: (field third = #integer ifTrue: ['platform integer'] ifFalse: ['platform text']);
                nextPutAll: ')'.
            field size = 4 ifTrue: [s nextPutAll: ' bePrimaryKey'].
            s nextPutAll: '. ' ] ]).
    descriptors compile: (String streamContents: [ :s |
        s nextPutAll: 'classModelFor'; nextPutAll: modelName; nextPutAll: ': model '.
        fields do: [ :field |
            s nextPutAll: 'model newAttributeNamed: '; print: field first;
                nextPutAll: ' type: '; nextPutAll: (field third = #integer ifTrue: ['Integer'] ifFalse: ['String']);
                nextPutAll: '. ' ] ]).
    descriptors compile: (String streamContents: [ :s |
        s nextPutAll: 'descriptorFor'; nextPutAll: modelName; nextPutAll: ': descriptor | table | table := self tableNamed: ';
            print: tableName asString; nextPutAll: '. descriptor table: table. '.
        fields do: [ :field |
            s nextPutAll: '(descriptor newMapping: DirectMapping) from: '; print: field first;
                nextPutAll: ' to: (table fieldNamed: '; print: field second asString; nextPutAll: '). ' ] ]) ].
(Object << #BenchInventory) slots: #(database writers benchmark); package: package; install!
!BenchInventory methodsFor: 'persistence'!
database: path benchmark: flag
    database := path.
    benchmark := flag.
    writers := Semaphore forMutualExclusion
!
withDatabase: operation
    | file login accessor session system |
    file := database asFileReference absolutePath asFileReference.
    login := Login new database: UDBCSQLite3Platform new; host: file parent fullName;
        password: ''; databaseName: file basename; yourself.
    accessor := DatabaseAccessor forLogin: login.
    accessor login.
    ^ [ accessor executeSQLString: 'PRAGMA foreign_keys = ON'.
        accessor executeSQLString: 'PRAGMA busy_timeout = 10000'.
        system := BenchInventoryDescriptors forPlatform: login database.
        session := GlorpSession forSystem: system.
        session accessor: accessor.
        operation value: session value: accessor ] ensure: [ accessor logout ]
!
migrate: path
    self withDatabase: [ :session :accessor |
        (path contents splitOn: $;) do: [ :statement |
            statement trimBoth ifNotEmpty: [accessor executeSQLStringNoResult: statement] ] ]
!
seedA: a b: b
    self withDatabase: [ :session :accessor |
        session inUnitOfWorkDo: [
            { 'A' -> a. 'B' -> b } do: [ :pair |
                (session readOneOf: BenchStock where: [ :row | row sku = pair key ])
                    ifNil: [session register: (BenchStock new sku: pair key; available: pair value; yourself)] ] ] ]
!
error: status code: code
    ^ {status. Dictionary newFrom: {'error' -> (Dictionary newFrom: {'code' -> code})}}
!
itemsFor: id session: session
    ^ (session read: BenchItem where: [ :row | row reservationId = id ])
        sorted: [ :a :b | a sku < b sku ]
!
render: reservation items: items
    ^ Dictionary newFrom: {
        'id' -> reservation id. 'status' -> reservation status. 'expires_at' -> reservation expires.
        'items' -> (items asArray collect: [ :item | Dictionary newFrom: {
            'sku' -> item sku. 'quantity' -> item quantity } ]) }
!
write: key path: path body: body operation: operation session: session accessor: accessor
    | encoded fingerprint |
    encoded := (key asByteArray hex).
    fingerprint := STONJSON toString: {'POST'. path. body}.
    ^ writers critical: [
        session inTransactionDo: [
            accessor executeSQLStringNoResult: 'UPDATE clock SET now = now WHERE id = 1'.
            session inUnitOfWorkDo: [
                | previous result clock |
                previous := session readOneOf: BenchResponse where: [ :row | row key = encoded ].
                previous ifNotNil: [
                    previous fingerprint = fingerprint
                        ifTrue: [{previous status. STONJSON fromString: previous body}]
                        ifFalse: [self error: 409 code: 'idempotency_conflict'] ]
                ifNil: [
                    clock := session readOneOf: BenchClock where: [ :row | row id = 1 ].
                    result := operation value: clock.
                    result first = 400 ifFalse: [
                        session register: (BenchResponse new key: encoded; fingerprint: fingerprint;
                            status: result first; body: (STONJSON toString: result second); yourself) ].
                    result ] ] ] ]
!
reserve: body clock: clock session: session
    | expiry items stocks id reservation lineItems |
    expiry := clock now + (body at: 'ttl').
    expiry > 2000000000 ifTrue: [^ self error: 400 code: 'invalid_request'].
    items := body at: 'items'.
    stocks := items collect: [ :item | session readOneOf: BenchStock
        where: [ :row | row sku = (item at: 'sku') ] ].
    (stocks includes: nil) ifTrue: [^ self error: 404 code: 'unknown_sku'].
    items with: stocks do: [ :item :stock |
        stock available < (item at: 'quantity') ifTrue: [^ self error: 409 code: 'insufficient_stock'] ].
    id := UUID new asString.
    reservation := BenchReservation new id: id; status: 'pending'; expires: expiry; yourself.
    session register: reservation.
    lineItems := OrderedCollection new.
    items with: stocks do: [ :item :stock |
        | line quantity |
        quantity := item at: 'quantity'.
        session register: stock.
        stock available: stock available - quantity.
        line := BenchItem new reservationId: id; sku: stock sku; quantity: quantity; yourself.
        session register: line.
        lineItems add: line ].
    ^ {201. self render: reservation items: lineItems}
!
transition: id status: status session: session
    | row items |
    row := session readOneOf: BenchReservation where: [ :each | each id = id ].
    row ifNil: [^ self error: 404 code: 'unknown_reservation'].
    row status = 'pending' ifFalse: [^ self error: 409 code: 'invalid_transition'].
    items := self itemsFor: id session: session.
    status = 'confirmed' ifFalse: [ items do: [ :item |
        | stock |
        stock := session readOneOf: BenchStock where: [ :each | each sku = item sku ].
        session register: stock.
        stock available: stock available + item quantity ] ].
    session register: row.
    row status: status.
    ^ {200. self render: row items: items}
!
advance: seconds clock: clock session: session accessor: accessor
    | now totals expired |
    now := clock now + seconds.
    now > 2000000000 ifTrue: [^ self error: 400 code: 'invalid_request'].
    totals := accessor executeSQLString:
        'SELECT i.sku, SUM(i.quantity)
         FROM reservation_items i JOIN reservations r ON r.id = i.reservation_id
         WHERE r.status = ? AND r.expires_at <= ? GROUP BY i.sku'
        binding: {'pending'. now}.
    totals do: [ :row |
        accessor executeSQLString:
            'UPDATE stock SET available = available + ? WHERE sku = ?'
            binding: {(row atIndex: 2). (row atIndex: 1)} ].
    accessor executeSQLString:
        'UPDATE reservations SET status = ? WHERE status = ? AND expires_at <= ?'
        binding: {'expired'. 'pending'. now}.
    expired := (accessor executeSQLString: 'SELECT changes()') first first.
    session register: clock.
    clock now: now.
    ^ {200. Dictionary newFrom: {'now' -> now. 'expired' -> expired}}
! !
!BenchInventory methodsFor: 'http'!
identifier: value size: limit lowercase: lowercase
    ^ value isString and: [(value size between: 1 and: limit) and: [
        value allSatisfy: [ :c | ('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' includes: c)
            or: [lowercase and: ['abcdefghijklmnopqrstuvwxyz' includes: c]] ] ] ]
!
positive: value
    ^ value isInteger and: [value between: 1 and: 1000000]
!
normalizedReserve: body
    | items seen sorted |
    (body isDictionary and: [body size = 2 and: [(body includesKey: 'items') and: [
        self positive: (body at: 'ttl' ifAbsent: [nil])]]]) ifFalse: [^ nil].
    items := body at: 'items'.
    (items isArray and: [items size between: 1 and: 100]) ifFalse: [^ nil].
    seen := Set new.
    items do: [ :item |
        | sku |
        (item isDictionary and: [item size = 2]) ifFalse: [^ nil].
        sku := item at: 'sku' ifAbsent: [nil].
        ((self identifier: sku size: 32 lowercase: false) and: [
            (seen includes: sku) not and: [self positive: (item at: 'quantity' ifAbsent: [nil])]])
            ifFalse: [^ nil].
        seen add: sku ].
    sorted := (items sorted: [ :a :b | (a at: 'sku') < (b at: 'sku') ])
        collect: [ :item | OrderedDictionary newFrom: {'sku' -> (item at: 'sku'). 'quantity' -> (item at: 'quantity')} ].
    ^ OrderedDictionary newFrom: {'items' -> sorted asArray. 'ttl' -> (body at: 'ttl')}
!
bodyFrom: request
    request hasEntity ifFalse: [^ Dictionary new].
    request entity contentLength > 65536 ifTrue: [^ nil].
    (request contentType matches: ZnMimeType applicationJson) ifFalse: [^ nil].
    ^ [STONJSON fromString: request contents] on: Error do: [nil]
!
dispatch: request
    | path parts key body |
    parts := request uri segments.
    path := '/', ('/' join: parts).
    ((request method = #GET) and: [path = '/healthz']) ifTrue: [
        ^ [self withDatabase: [ :session :accessor |
            (session readOneOf: BenchClock where: [ :row | row id = 1 ])
                ifNil: [{503. Dictionary newFrom: {'status' -> 'unavailable'}}]
                ifNotNil: [{200. Dictionary newFrom: {'status' -> 'ready'}}] ]]
            on: Error do: [{503. Dictionary newFrom: {'status' -> 'unavailable'}}] ].
    request method = #GET ifTrue: [
        ^ self withDatabase: [ :session :accessor |
            ((parts size = 3) and: [parts first = 'v1' and: [parts second = 'stock']])
                ifTrue: [ | sku row |
                    sku := parts third.
                    (self identifier: sku size: 32 lowercase: false) ifFalse: [^ self error: 400 code: 'invalid_request'].
                    row := session readOneOf: BenchStock where: [ :each | each sku = sku ].
                    row ifNil: [^ self error: 404 code: 'unknown_sku'].
                    {200. Dictionary newFrom: {'sku' -> sku. 'available' -> row available}} ]
                ifFalse: [
                    ((parts size = 3) and: [parts first = 'v1' and: [parts second = 'reservations']])
                        ifTrue: [ | id row |
                            id := parts third.
                            row := session readOneOf: BenchReservation where: [ :each | each id = id ].
                            row ifNil: [^ self error: 404 code: 'unknown_reservation'].
                            {200. self render: row items: (self itemsFor: id session: session)} ]
                        ifFalse: [self error: 404 code: 'not_found'] ] ] ].
    request method = #POST ifFalse: [^ self error: 404 code: 'not_found'].
    key := request headers at: 'Idempotency-Key' ifAbsent: [nil].
    (self identifier: key size: 128 lowercase: true) ifFalse: [^ self error: 400 code: 'invalid_request'].
    body := self bodyFrom: request.
    ^ self withDatabase: [ :session :accessor |
        | operation normalized |
        normalized := body.
        path = '/v1/reservations' ifTrue: [
            normalized := self normalizedReserve: body.
            normalized ifNil: [^ self error: 400 code: 'invalid_request'].
            operation := [ :clock | self reserve: normalized clock: clock session: session ] ].
        ((parts size = 4) and: [parts first = 'v1' and: [parts second = 'reservations'
            and: [#('confirm' 'cancel') includes: parts fourth]]]) ifTrue: [
                (body isDictionary and: [body isEmpty]) ifFalse: [^ self error: 400 code: 'invalid_request'].
                operation := [ :clock | self transition: parts third
                    status: (parts fourth = 'confirm' ifTrue: ['confirmed'] ifFalse: ['cancelled'])
                    session: session ] ].
        (benchmark and: [path = '/v1/clock/advance']) ifTrue: [
            (body isDictionary and: [body size = 1 and: [self positive: (body at: 'seconds' ifAbsent: [nil])]])
                ifFalse: [^ self error: 400 code: 'invalid_request'].
            operation := [ :clock | self advance: (body at: 'seconds') clock: clock session: session accessor: accessor ] ].
        operation ifNil: [^ self error: 404 code: 'not_found'].
        self write: key path: path body: normalized operation: operation session: session accessor: accessor ]
!
handleRequest: request
    | result |
    result := [ self dispatch: request ] on: Error do: [ :exception |
        Stdio stderr nextPutAll: exception messageText asByteArray; nextPut: 10; flush.
        self error: 500 code: 'internal_error' ].
    ^ (ZnResponse statusCode: result first)
        entity: (ZnEntity json: (STONJSON toString: result second)); yourself
! !
!BenchInventory class methodsFor: 'command line'!
options: arguments
    | options positional pending |
    options := Dictionary new.
    positional := OrderedCollection new.
    arguments do: [ :arg |
        pending ifNotNil: [
            pending = '--item'
                ifTrue: [(options at: pending ifAbsentPut: [OrderedCollection new]) add: arg]
                ifFalse: [options at: pending put: arg].
            pending := nil ]
        ifNil: [
            arg = '--benchmark-mode'
                ifTrue: [options at: arg put: true]
                ifFalse: [(arg beginsWith: '--')
                    ifTrue: [pending := arg]
                    ifFalse: [positional add: arg]] ] ].
    pending ifNotNil: [self error: 'Missing argument'].
    ^ {options. positional}
!
client: arguments
    | parsed options positional verb path target base body client response status |
    parsed := self options: arguments.
    options := parsed first.
    positional := parsed second.
    verb := positional first.
    target := positional at: 2 ifAbsent: [nil].
    base := options at: '--base-url' ifAbsent: ['http://127.0.0.1:8080'].
    verb = 'health' ifTrue: [path := '/healthz'].
    verb = 'stock' ifTrue: [path := '/v1/stock/', target urlEncoded].
    verb = 'reservation' ifTrue: [path := '/v1/reservations/', target urlEncoded].
    (#('confirm' 'cancel') includes: verb) ifTrue: [
        path := '/v1/reservations/', target urlEncoded, '/', verb.
        body := Dictionary new ].
    verb = 'reserve' ifTrue: [
        path := '/v1/reservations'.
        body := Dictionary newFrom: {
            'ttl' -> (options at: '--ttl') asInteger.
            'items' -> ((options at: '--item') collect: [ :arg |
                | pair |
                pair := arg splitOn: $=.
                pair size = 2 ifFalse: [self error: 'Expected SKU=QUANTITY'].
                Dictionary newFrom: {'sku' -> pair first. 'quantity' -> pair second asInteger} ]) asArray } ].
    verb = 'advance' ifTrue: [
        path := '/v1/clock/advance'.
        body := Dictionary newFrom: {'seconds' -> target asInteger} ].
    path ifNil: [self error: 'Unknown client command'].
    client := ZnClient new url: ((base endsWith: '/') ifTrue: [base allButLast] ifFalse: [base]), path.
    body ifNil: [client method: #GET] ifNotNil: [
        client method: #POST;
            headerAt: 'Idempotency-Key' put: (options at: '--request-id');
            entity: (ZnEntity json: (STONJSON toString: body)) ].
    client execute.
    response := client response.
    status := response statusLine code.
    Stdio stdout nextPutAll: response contents asByteArray; nextPut: 10; flush.
    ^ (status between: 200 and: 299) ifTrue: [0] ifFalse: [1]
!
main: arguments source: source
    | parsed options positional url inventory verb |
    arguments first = 'client' ifTrue: [^ self client: arguments allButFirst].
    parsed := self options: arguments.
    options := parsed first.
    positional := parsed second.
    verb := positional first.
    url := options at: '--database-url' ifAbsent: ['sqlite:///inventory.db'].
    (url beginsWith: 'sqlite:///') ifFalse: [self error: 'This build supports sqlite:/// URLs'].
    PharoDatabaseAccessor DefaultDriver: SQLite3Driver.
    inventory := self new database: (url allButFirst: 10)
        benchmark: (options at: '--benchmark-mode' ifAbsent: [false]); yourself.
    verb = 'migrate' ifTrue: [inventory migrate: source parent / 'migration-0001.sql'. ^ 0].
    verb = 'seed' ifTrue: [
        | a b |
        a := (options at: '--stock-a' ifAbsent: ['10']) asInteger.
        b := (options at: '--stock-b' ifAbsent: ['5']) asInteger.
        ((a between: 0 and: 2000000000) and: [b between: 0 and: 2000000000])
            ifFalse: [self error: 'Invalid initial stock'].
        inventory seedA: a b: b.
        ^ 0 ].
    verb = 'serve' ifTrue: [
        | server |
        (options at: '--host' ifAbsent: ['127.0.0.1']) = '127.0.0.1'
            ifFalse: [self error: 'Use the loopback host for this submission'].
        server := ZnServer on: (options at: '--port' ifAbsent: ['8080']) asInteger.
        server bindingAddress: #[127 0 0 1].
        server delegate: inventory.
        server start.
        [Semaphore new wait] ensure: [server stop].
        ^ 0 ].
    self error: 'Expected migrate, seed, serve or client'
! !
| commandLine separator |
commandLine := Smalltalk arguments.
separator := commandLine indexOf: '--'.
separator > 0 ifTrue: [
    Smalltalk exit: ([BenchInventory main: (commandLine allButFirst: separator)
        source: (commandLine at: separator - 1) asFileReference]
        on: Error do: [ :failure |
            Stdio stderr nextPutAll: failure messageText asByteArray; nextPut: 10; flush.
            2 ]) ]!
