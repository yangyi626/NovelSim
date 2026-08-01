# NovelSim Planner Expert Dataset v1

- Manifest: `split_0b3fb988497f14b6`
- Code commit: `f6f9f20`
- Episodes: `2160`
- Decision steps: `9120`
- Objective success: `2160/2160`
- Illegal proposals: `720`
- Illegal commits: `0`
- Replay consistent: `2160/2160`

| Split | Episodes | Steps | Training access |
|---|---:|---:|---|
| train | 1080 | 4680 | allowed |
| dev | 120 | 520 | allowed |
| test_id | 240 | 1040 | sealed |
| test_ood | 720 | 2880 | sealed |

Only `train` may be consumed by SFT/GRPO training. `dev` is for checkpoint/reward selection; `test_id` and `test_ood` remain sealed.
