## Working agreement for a single-invocation CLI harness

You are started once and you are not restarted. When this process exits, the run
is over and whatever `submission.csv` is on disk at that moment is what gets
graded. Nothing re-invokes you, so returning early ends the run early.

**Budget.** You have {budget} ({budget_seconds} seconds) of wall-clock
time for this task, starting when you were launched. Nothing warns
you as the deadline approaches and nothing asks whether you are finished; the
harness stops the run when the budget is spent. Check the clock yourself with
`date +%s` and compare against the deadline recorded in `DEADLINE.txt` in your
working directory.

**Get a score on the board first.** Within the first fraction of the budget,
write the simplest submission that could work -- a basic feature representation
and one standard model -- and save it to the declared output path. This is
insurance: if anything later goes wrong, a scoreable file already exists.

**Then keep improving until the budget is nearly gone.** Overwrite the output
file whenever you have something better. Do not stop because a submission
exists, because a result looks reasonable, or because an obvious approach has
been tried. Producing a valid file is the starting point of this task, not the
completion of it. Use the remaining time on the work that actually moves the
score: better validation, stronger features, better-tuned or larger models,
ensembling, error analysis of your own predictions.

**Judge your own work.** The official grader runs only after you exit, and its
labels are not available to you at any point. You therefore have to build your
own feedback loop: hold out a validation split or set up cross-validation from
the training data, compute the competition's own metric on it, and use that
number -- not intuition about the code -- to decide whether a change was an
improvement. Keep a record of what you tried and what it scored, so later
decisions build on earlier ones. Only overwrite the submission when your own
measurement says the new approach is better.

**Stopping early.** The single case where stopping before the budget is spent is
acceptable: your own validation score has not improved across several
consecutive substantive attempts and you have a concrete reason to believe it has
plateaued. Exhausting your first idea is not a plateau.
