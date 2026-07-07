# LexBench-Browser2.0 Experiment Report

This is the experiment report for **LexBench-Browser2.0**. It summarizes the initial
validation run used to check whether LexBench-Browser2.0 separates stronger and weaker
browser agents in a meaningful way.

## Setup

- **Task set**: a 90-task human-reviewed validation subset of LexBench-Browser2.0.
- **Review criteria**: reviewers checked that task queries were grounded, rubrics constrained
  completion without hard-coded live values, and tasks did not require login state or CAPTCHA
  solving.
- **Agent input**: DOM-only browser observations.
- **Step budget**: 100 steps per task.
- **Judge policy**: rubric evaluation with all-or-nothing task success. A task is counted as
  successful only when all rubric requirements are satisfied.
- **Models tested**: GPT-5.5 and Gemini 3.5 Flash.

## Results

These numbers are an initial validation result, not an official leaderboard submission.

| Metric | GPT-5.5 | Gemini 3.5 Flash | Difference |
| --- | ---: | ---: | ---: |
| Success rate | 58.9% (53/90) | 46.7% (42/90) | -12.2 points |
| Average steps, all tasks | 17.1 | 46.4 | +29.3 steps |
| Average steps, successful tasks | 15.5 | 42.7 | +27.2 steps |

## Interpretation

The benchmark exposes both success-rate and efficiency differences. GPT-5.5 completed more
tasks while using far fewer steps. In manual review, GPT-5.5 more often used direct URL
construction or targeted navigation, while Gemini 3.5 Flash more often followed longer
UI-click paths, repeated searches, or switched tabs in ways that lost context.

The result suggests LexBench-Browser2.0 is effective at measuring more than final answer
formatting: it distinguishes retrieval efficiency, multi-source instruction following, and
whether the agent can make clear comparative decisions required by the query.

## Additional Quality Checks

A separate rubric-auditor prompt was tested on 30 reviewed cases for identifying query/rubric
issues, reaching 96.7% accuracy (29/30). This was used as a supporting QA signal, not as a
replacement for human review.
