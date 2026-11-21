# Usage

## The three things you do

1. Put documents in a collection.
2. Ask questions.
3. Read the citations.

Everything else is optional.

## Adding documents

Sign in, pick a collection in the sidebar (or create one), then **Add document**.
Paste a title and the text.

The text can be anything: a policy, a README, meeting notes, a support macro, a
contract. It does not need to be clean — hard-wrapped lines from a PDF or an
email are unwrapped automatically before chunking.

**What makes a good document:**

- **One topic per document.** A 40-page handbook chunked as one blob retrieves
  poorly. Split it by section.
- **Keep the headings.** "Refund Policy" as a title helps retrieval more than
  you would expect, because the title is part of the indexed text.
- **Include the specifics.** "Refunds take a while" is unanswerable. "Refunds
  are issued within 30 days" is answerable.

## Asking questions

Type a question in plain language. The system does not need keywords.

**Questions that work well:**

- "How long do refunds take?"
- "Who approves expenses over 50,000?"
- "What are the support hours?"
- "Can I get a refund on a gift card?"

**Questions that work less well:**

- "Tell me everything about refunds" — this is a request for a summary, not a
  question with an answer in one sentence. The system will return the most
  relevant sentences, which may not feel like a summary.
- "Why did we change the policy?" — the *reason* for a change is rarely written
  down. If it is not in the documents, you get a refusal.

## Reading the answer

Each sentence in the answer ends with a number in brackets. That number maps to
a citation below, which shows:

- the source document title
- the exact sentence
- its position in the document

Click a citation to see the surrounding text.

## When it refuses

A refusal is not a failure. It means one of three things, and the message says
which:

**"The knowledge base does not cover this question. Missing from every
retrieved passage: X, Y."**
The words that make your question specific do not appear in any document. Add a
document that covers the topic.

**"Retrieval was too weak to answer from."**
The terms appear, but the passages are only loosely related. Usually means the
question is phrased very differently from the source. Try rephrasing with words
the document would use.

**"Passages mention these terms but no single sentence answers the question."**
The information is scattered but never stated. This is the most useful refusal:
it tells you the document is incomplete rather than that you asked wrong.

## The "How was this answered?" panel

Expand it to see the numbers behind the decision:

- **Coverage** — what fraction of your question's weighted terms appear in the
  retrieved passages
- **Retrieval strength** — how strongly the best passage matched
- **Matched terms** — which words were found
- **Missing terms** — which were not

If an answer looks wrong, this panel usually explains why in one glance.

## The stats panel

The sidebar shows documents, chunks, questions asked, and answer rate.

`unanswered_questions` in `GET /api/stats` is the most useful thing in the app.
It is a list of every question your knowledge base could not answer — a direct
to-do list of documents to write.

## Tips

**Ask one thing at a time.** "What are the refund and expense policies?" is two
questions. You will get an answer to whichever is better represented.

**Use the words the document uses.** The system stems plurals and common verb
endings, so "refund" finds "refunds" and "escalate" finds "escalation". It does
not know synonyms — "cost" will not find "price" unless the document says both.

**Check the citations, not just the answer.** The answer is assembled from
sentences; the citations are the ground truth. If a citation does not support
the sentence it is attached to, that is a bug worth reporting.

**Add documents when you get refusals.** The refusal message names the missing
terms. That is the search query for the document you need to write.
