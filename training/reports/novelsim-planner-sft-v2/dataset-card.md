# NovelSim Planner SFT Dataset v2

- Format: conversational prompt-completion
- Loss: completion only
- Prompt version: `novelsim_planner_prompt.v2`
- Train samples: `3060`
- Dev samples: `340`
- Train/Dev hash overlap: `0`
- Sealed and not loaded: `test_id, test_ood, adversarial`

Rejected/illegal proposals are excluded as targets. Their structured feedback is retained on the next legal recovery action.
