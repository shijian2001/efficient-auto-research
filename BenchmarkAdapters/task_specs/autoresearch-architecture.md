# AutoResearch Architecture Design Same-Model Agent Framework Track

Edit only `train.py` under the frozen benchmark policy. Use the host-owned development evaluator for candidate feedback and leave one declared final revision. Do not read or modify prepared data, evaluator code, seed policy, protocol assets, or held-out evaluation records. The host replays the development-selected artifact and evaluates it on both frozen held-out seeds; held-out results never participate in candidate selection.
