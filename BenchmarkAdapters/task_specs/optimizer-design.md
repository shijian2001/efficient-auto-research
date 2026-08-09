# modded-NanoGPT Optimizer Design Same-Model Agent Framework Track

Edit only the frozen Track 3 `train_gpt_simple.py` artifact. Changes are limited to the optimizer implementation, optimizer hyperparameters and schedules, model initialization, and the protocol-permitted literal training-step bound. Dataset, batch size, model architecture, distributed setup, validation, and one-forward-backward-per-step semantics remain frozen. Use only development evaluation during search. The host replays the development-selected artifact on both held-out seeds using all four allocated GPUs.
