# AI-Powered Gym Progress Tracker

**Student:** Zac | **Course:** Data Science & Analytics, FAU

## Problem
Lifters log numbers but rarely learn from them. It's hard to tell whether a stall is real, what drives good vs. bad sessions, or when a PR is realistic.

## Goal
Build a tool that tracks training and recovery data, models performance, and uses AI to produce plain-English insights and recommendations.

## Data
- **Per set:** date, exercise, weight, reps, RPE (1-10)
- **Per day:** sleep hours, bodyweight, soreness (1-10), energy (1-10), optional protein and time of day
- **Source:** my own training logs (real data), plus OpenPowerlifting public dataset for benchmarking

## Methods
1. **Feature engineering:** estimated 1RM per set (Epley: `weight * (1 + reps/30)`), weekly volume, rolling averages
2. **Trend + plateau detection:** rolling regression slope on e1RM; flag stalls longer than N weeks
3. **Predictive model:** linear regression, then random forest, to predict next-session e1RM from recovery and training load
4. **Driver analysis:** correlation / feature importance for sleep, soreness, time of day
5. **LLM layer:** send the weekly summary to an LLM to generate insights and suggest progression or a deload

## Deliverables
- Cleaned dataset and reproducible Python analysis (pandas, scikit-learn, matplotlib)
- Streamlit dashboard: e1RM trends, plateau flags, model predictions, AI weekly summary
- Short report: methods, results, limitations

## Evaluation
- Model error (MAE/RMSE) on held-out sessions vs. a naive baseline (last session's e1RM)
- Does the plateau detector agree with my own sense of when I stalled?

## Timeline
| Week | Milestone |
|------|-----------|
| 1 | Finalize data template, start logging, import any old logs |
| 2 | Cleaning + e1RM + charts |
| 3 | Plateau detection + baseline model |
| 4 | Better model + driver analysis |
| 5 | LLM weekly summary + Streamlit dashboard |
| 6 | Report + presentation |

## Risks
- **Small dataset:** use simple models, cross-validation, and be upfront about limits
- **Inconsistent logging:** keep the template minimal and fast to fill in
- **LLM advice isn't medical advice:** frame outputs as suggestions, not prescriptions
