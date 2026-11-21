# Program 1 · Live development journal

Agent: GPT-6.2-sol with Codex. Task: change the order discount from 10% at
10 units to 15% at 8 units. Seed A with 9 units and B with 12 units after
the worker starts. Keep those orders in memory while editing and applying
the policy. These are observations from the development session.

| Step | A (cents) | B (cents) |
| --- | ---: | ---: |
| Original policy | 11250 | 13500 |
| Revised source saved on disk | 11250 | 13500 |
| Revised code applied in the same process | 9562 | 12750 |
| Fresh process from the revised source | 9562 | 12750 |

The fresh process seeds the same inputs for replay. The live process seeds
only once, before the edit. The driver stages the revised file in a temporary
directory while the process is running. The policy files in Git provide the
starting point and the reproducible revision.

## SBCL 2.5.0

Reused a hash table for the orders and moved the discount to a separate
function file. Loading the edited file replaced the function's definition.
The quote handler resolved that definition on the next request, and the
existing orders returned the new totals. The first live-workflow run passed,
including the fresh-process replay. The main productivity gain was being
able to test a change against state that was already set up.

## ECL 24.5.10

Ran the same Common Lisp worker and policy files. The first live-workflow
run passed without ECL-specific edits. The earlier executable experiment
used a separate native build. Here the running ECL session loaded the
revised Lisp file directly, so the native packaging step was outside the
edit-and-check loop.

## Pharo 13.1

Created a policy class and one policy instance inside the headless image.
The first run failed before readiness because the installed Pharo image
didn't recognize the older class-definition selector I used. The class
builder using `Object << #LiveQuotePolicy` fixed that.

The reload command compiled the edited method into the live class. The
same policy instance answered the next message with the new behavior, and
the orders dictionary stayed in memory. The changed method also worked
in a fresh process using the original Pharo image and the revised method
file. No modified image had to be saved. The setup cost was finding the
correct class API. Once the class existed, applying the method change was
one operation.

## Python 3.11 · Earlier REPL comparison

In the earlier quote demo, re-executing `def discount_rate` updated the
global name used by `quote_order`. The dictionary and its orders remained
in the same interpreter. That gives this particular example much of the
same fast feedback as redefining a Lisp function. Pharo's exercise went
one step further structurally by changing the method of an existing class
and sending the same message to an existing instance.

## Rust 1.86 · Earlier compiled comparison

The earlier Rust example passed the policy as a function argument. It
applied both prewritten policies to the same order map in one process.
That demonstrates policy replacement using a designed extension point.
Editing a policy in the source and getting it into the executable still
required a build in that workflow. We did not test a Rust REPL or a
dynamic reload mechanism.

## Replay

From the repository root, with the Roswell versions above and the Pharo
image installed:

```sh
python3 -B development/live-image/verify.py sbcl
python3 -B development/live-image/verify.py ecl
PHARO_VM=/path/to/pharo PHARO_IMAGE=/path/to/Pharo.image \\
  python3 -B development/live-image/verify.py pharo
```

The reload commands are for trusted local development. The program
benchmark uses its own worker and results.
