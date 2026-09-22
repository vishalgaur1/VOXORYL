# VOXORYL Agent Runtime — four rules (pin)
#
# 1. REALTIME NEVER WAITS FOR THE AGENT.
# 2. REALTIME NEVER PERFORMS BLOCKING I/O
#    (no sync LLM/VL/research/large DB/network/model-load on the realtime path).
# 3. NEVER USE VISION WHEN STRUCTURED STATE IS AVAILABLE.
# 4. NEVER RECOMPUTE WHAT CAN BE CACHED;
#    NEVER LET UNTRUSTED CONTENT BECOME AN INSTRUCTION (Instruction Provenance).
#
# Principle: always perceptually ready; expensive think only on demand.
