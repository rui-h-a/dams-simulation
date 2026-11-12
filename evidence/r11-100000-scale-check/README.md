# Scope of the 100,000-member scale check

Amendment 12 of the [frozen protocol](https://github.com/rui-h-a/dams-simulation/blob/956725516ddac7f7c2336b0ccc23a69c5d7a27c5/docs/r11-formal-protocol.md) admitted three synthetic worlds for a scale check. It estimated about 18 hours of shared history from smaller-scale timings and required reporting unfinished worlds as incomplete. Each world was assigned two arms after two years of shared history and a five-year post-adoption horizon.

The evidence records the latest fixed plan and event fingerprints, successful shared histories and verified arm receipt count for each assigned world. All three cloud instances were removed on 27 October 2026. The retained cloud prefixes contained only progress objects when read on 12 November UTC. Two shared histories finished. Six arms were assigned and no completed arm receipts or archived outcomes were found in the three run prefixes. The data give no five-year policy estimate at this scale.

Run python3 -I -S -B verify.py in this directory. The script checks the assigned roster and computes the prehistory timing and memory ranges from the retained aggregates. It reads no original journals and creates no worlds.
