# Image Descriptions Visualization

Status: draft
Follow-ups: docs/superpowers/specs/2026-07-18-image-descriptions-display-design.md, docs/superpowers/specs/2026-07-18-similarity-mode-toggle-design.md, docs/superpowers/specs/2026-07-19-description-feedback-design.md

# Summary

In [Multi-prompt image descriptions spec](docs/superpowers/plans/2026-07-13-multi-prompt-image-descriptions.md) we defined the process of AI-description generation for memes, which is further used in [Image Description Embeddings Similarity](docs/superpowers/plans/2026-07-16-image-description-embeddings-similarity.md) to define an alternative similarity for memes, based on semantics captured by AI.

Next logical step is:
- Showing descriptions to the user
- Employing alternative similarity search (already supported in [Backend API](backend_api.md))
  - A setting in Android Client
  - A separate tab for "Similar images" - Visual similarity (existing, CLIP embeddings based) and Semantic (Qwen)
- Ability to "Approve" (or "like") description or "Reject" (or "dislike") - both Web and mobile

Ability to provide a binary feedback would help in the future to re-assess descriptions and track description quality, also tuning the process and prompts (now there is no quality strategy defined for it)

# Possible further directions, yet out of scope

- Further extension of this functional may include editing description - in that case we would need to mark description as user-sourced and not delete it when we rerun full descriptions batch (we would only need to delete AI-generated descriptions)
- We could also want to have multiple different models used for descriptions so that we can compare

# Concerns and ambiguities

- How do we display it? The option I suggested might not be the best one. Separate analytics agent should work on it and suggest alternatives
- Do we want it different in Web and Mobile (Android)?
- This functionality classifies as "Admin" or "Admin approval needed", which not yet exists because only one user exists, but it could change in the future. Separate documentation for all that "admin" functionality is needed to track