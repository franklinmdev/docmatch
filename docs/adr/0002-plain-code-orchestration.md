---
status: accepted
date: 2026-09-24
---

# The loop is plain code over the document table, with no graph library and no job queue

Phase 4 owes a written decision on whether a graph orchestration library earns
its place for pause-and-resume. It does not. The loop is plain functions over
the status machine #150 settled, and the documents table is the only queue.
This was decided on #155 against the facts gathered on #149 (LangGraph) and
#147 (Postgres queues).

**What the loop is.** Moving a document from one status to the next is a function that
reads the output saved at the document's current status, computes the next
one, and writes it together with its transition in one transaction under
compare-and-set (`WHERE status = <expected>`). Pausing at `needs_review` means
nothing runs, because the document is simply a row in that status. Approving or
rejecting is one transition written by the API. A retaken
document starts again from its last saved status, so it never reads the
document twice.

**Why no graph library.** LangGraph 1.2.12 with langgraph-checkpoint-postgres
3.1.2 was weighed as the alternative. There, `interrupt()` pauses the graph and
`Command(resume=...)` resumes it. That approach was rejected for three reasons:

1. Its checkpointer keeps graph state in four tables of its own, which would be
   a second record of where a document stands beside the status column. #150
   rejected exactly that.
2. A resume reruns the whole node from the top, so the idempotency has to be
   written by hand anyway. The compare-and-set already gives it.
3. No primary source guarantees what happens when two callers resume one
   thread at the same time (unverified on #149).

The library would add `langgraph`, its Postgres checkpointer, and seven packages
the engine does not already have, `langchain-core` among them (#149), without
removing any code. Declaring the order with `StateGraph` and keeping the state
in our tables was also rejected, because the path is a line with two shortcuts to review
and a declared graph buys nothing over it.

**Why no job queue.** A pending document is one whose status is neither final
nor `needs_review`. A worker claims it with `FOR UPDATE SKIP LOCKED` and a
lease column (a claim that lapses at a set time), and another worker retakes it
when the lease lapses. pgqueuer 1.4.0, the one #147's research leaned toward,
was rejected because its job tables would be a second record of what is
pending, and the compare-and-set already stops a retaken document from writing a
transition twice. The one repeat left, a reading paid for twice when a lease
lapses before extraction saves it, is the same under any queue that retakes
work. What this costs is ours to test: the claim, the lapse and the retake,
against a real Postgres the way the resolution tests already run. A
job table of our own was rejected for the same reason, at the cost of writing
it.

**A document that keeps failing.** Leases are counted per status. When the
third lease lapses at the same status, the document goes to `needs_review`
with the routing reason "pipeline failed", naming the status it stood at. This
is a second shortcut beside a failed extraction. Three matches the
extraction attempts #35 set, and routing keeps #150's rule that only a reviewer
rejects. Stopping the document where it stands was rejected, because then no
one would ever see it. Retrying forever was rejected, because in a loop measured
one document at a time (#152) a single bad document would stall the run. The
lease length is a constant for the spec.

**Where a correction reruns.** A reviewer's correction reruns the gate,
resolution and match (#150, #154) inside the HTTP request, calling the same
functions the worker calls. Gate, resolution and match are local and take milliseconds, so the
response carries the new result, and the review page (#156) needs no polling.
The API loads the embedder at startup. Routing the rerun through the worker was
rejected: it needs a marker inside `needs_review` and a page that polls.

**What reopens this.** The decision is revisited if a model comes to choose the
loop's next move (for example, deciding to re-read a page or to try another
backend), or if a second human pause appears partway along the path. Both
would make the path something code no longer fixes, which is the case a graph
library is built for. Adding another linear status does not reopen it.
