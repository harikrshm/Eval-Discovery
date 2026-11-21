# The 6 production traits, explained in simple words

You chose to make the MVP "close to production". That means six things beyond the demo logic. For each one you get: what it is, why we need it, how it works step by step, what you will see, and how to talk about it in an interview.

The running example: you open the app, paste the system prompt of a customer-support agent, chat with it a few times, and the app finds a repeated weakness, tests it, and proposes a fix.

---

## 1. Identity: an anonymous workspace (no login)

**In one line:** the app gives every visitor a private "locker" without asking them to sign up.

**Why:** once we store things, we must keep one person's pasted prompts and chats away from everyone else's. But a login screen would put a wall in front of anyone trying your live demo.

**How it works, step by step**
1. You open the app for the first time.
2. The browser asks the backend: "make me a workspace."
3. The backend creates a locker and sends back a long random code (the token).
4. Your browser saves the token. The backend saves only a scrambled version of it (a hash), the same way sites store passwords, so even someone reading the database cannot use it.
5. Every later request carries the token. The backend only ever shows you what is in your locker.
6. If you clear your browser data, you lose the token and the locker is unreachable. After 30 days it is deleted anyway.

**What you will see:** nothing. It just works. There is also a "Delete my data" button.

**Interview line:** "I chose anonymous tokens because the goal was zero friction for a demo, while still isolating data. Real auth is a later phase and the token model is the seam where it plugs in."

---

## 2. A real database (Postgres) with migrations

**In one line:** the app remembers things after you close the tab, and it can change how it remembers without losing data.

**Why:** today everything lives in the browser tab and vanishes when you close it. A production tool must keep agents, runs, criteria, tests and results.

**How it works, step by step**
1. Postgres is a database: tables with rows, like spreadsheets that the backend can search very quickly.
2. We make tables for the things in the product: workspace, agent, prompt version, run, criterion, test, test result, job, event.
3. A **migration** is a numbered, saved instruction like "add a column called X". Every change to the tables is a migration file kept in git.
4. When the backend deploys, it applies any migrations it has not applied yet, in order.
5. This means the live database and the code stay in step, and you can rebuild the database from scratch.

**What you will see:** reload the page tomorrow and your agent, runs and tests are still there.

**Things to check before building:** which managed Postgres to use. Free tiers have limits (storage, expiry, sleeping), so we will read the current terms first.

**Interview line:** "I treat the schema as code: every change is a reviewed migration, so deploys are repeatable."

---

## 3. Replays as background jobs

**In one line:** slow work happens "in the back", and the page checks in on it.

**Why:** a test replay means: re-run the conversation turn by turn, then have a judge grade it. That can take 10 to 30 seconds. If the page waits on a single request, a slow server or a timeout makes it feel broken. Also, our free server falls asleep and wakes up slowly, which can cut a long request in half.

**How it works, step by step**
1. You press **Replay**.
2. The backend writes a row in a `job` table: "replay test 7, status: queued" and answers immediately with the job's id.
3. A worker (a loop running inside the same backend) picks up queued jobs, marks one "running", and does the work: replays the messages, calls the judge, saves the result.
4. The page asks "is job 12 done?" every couple of seconds and shows "Running…".
5. When it is done, the page shows Pass or Fail, the judge's reasoning, and the new transcript.
6. If the server restarts mid-job, a job stuck in "running" for too long is put back in the queue and tried again (with a limit on retries).

**What you will see:** a spinner or "Running" state, then the verdict.

**Interview line:** "Long LLM chains shouldn't hold an HTTP request open. I used a database-backed queue because it adds no new infrastructure, and made jobs resumable because the host sleeps."

---

## 4. The event log

**In one line:** a diary of what users did, so the product's success can be measured.

**Why:** your strategy says success means fewer wasted preview runs and more tests saved. You can only claim that if the app records what happened.

**How it works, step by step**
1. At key moments the backend writes one small row: who (workspace), what (the event name), when, and details.
2. Events include: agent created, run saved, loop detected, criterion created, test created, replay passed or failed, fix suggested, fix approved or rejected.
3. A simple query turns these into the strategy doc's numbers: how often the nudge is accepted, how many runs until the first passing test, how often suggested fixes are approved.
4. Because there are no real users yet, we run **simulated builders** with and without the feature and label those numbers "simulated". We never present the strategy doc's illustrative figures as real results.

**What you will see:** a small metrics view or a query output in the demo.

**Interview line:** "I defined the metrics before building, and the events are named after them. I'm explicit that the numbers are simulated."

---

## 5. The CI eval gate

**In one line:** an automatic check that stops a bad prompt change from being merged.

**Why:** this is how software teams already work. Code changes run tests. A prompt change should too, because a prompt edit that fixes one problem can quietly break another.

**How it works, step by step**
1. You export your saved tests and the agent's prompt from the app. They are plain files kept in the customer's git repository.
2. A command-line tool reads them, replays every test against the current prompt, and has the judge grade each one.
3. It prints pass or fail per test and exits with an error if the pass rate falls under a threshold (for example 100%).
4. A GitHub Action runs this tool automatically whenever the prompt file changes in a pull request.
5. If a test fails, the pull request shows a red cross and cannot be merged until fixed or the threshold is deliberately changed.
6. It needs a Groq API key saved as a repository secret, never in the code.

**What you will see:** a pull request with a green or red check next to it.

**Interview line:** "The same pure core runs in the app and in CI, so a customer's team can gate prompt changes the same way they gate code."

---

## 6. What is deliberately NOT in the MVP: real login, billing, teams

**In one line:** we leave out the parts that add work but not learning.

**Why:** accounts, passwords, invitations, roles and payments are well-known problems that would take a big share of the time and add nothing to the idea you want to prove. Cutting them is a product decision, and you should say so.

**What stands in for them:** the anonymous workspace from item 1 keeps data separate. It is the single place real auth would plug in later.

**Interview line:** "I scoped out auth and billing on purpose and kept the seam for them. The MVP proves the loop, not account management."

---

## How the six fit together

```
You paste an agent  →  [1 workspace] keeps it private
chat + runs         →  [2 database] remembers them
Replay / suggest    →  [3 jobs] do the slow work in the back
everything above    →  [4 event log] records what happened
prompt changes      →  [5 CI gate] re-tests them automatically
accounts, billing   →  [6] left out on purpose
```

## Words you can reuse

- **Token:** a long secret code that proves "this browser owns this workspace".
- **Hash:** a one-way scramble. You can check a code against it but cannot get the code back.
- **Migration:** a saved, numbered change to the database structure.
- **Job / worker:** a to-do item for slow work, and the thing that picks it up.
- **Held-out test:** a second failing conversation that the fix was not tuned on, used to check the fix is real.
- **Judge:** an AI that grades a conversation against a criterion and explains why.
