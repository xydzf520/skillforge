# Bridge media template contract

Bridge only loads the three allow-listed files below from
`SKILLFORGE_MEDIA_TEMPLATE_DIR`; it never accepts a workflow graph from an API
caller:

- `h3_t2v_v1.json`
- `h3_i2v_v1.json`
- `h3_r2v_v1.json`

Each file is a JSON object with `version`, `model_version`, the fixed ComfyUI
`workflow`, and a platform-maintained `bindings` map. A binding contains one or
more existing JSON paths into the workflow, for example:

```json
{
  "version": "2026-08-11.1",
  "model_version": "MiniMax-H3",
  "workflow": {
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}
  },
  "bindings": {
    "positive_prompt": [["6", "inputs", "text"]]
  }
}
```

Allowed binding names are supplied by Bridge: `positive_prompt`,
`negative_prompt`, `audio_prompt`, `width`, `height`, `frames`, `fps`, `steps`,
`seed`, `batch_count`, `audio_enabled`, `reference_images`,
`reference_videos`, and `reference_audio`. Template files and model paths are
installed on the node and reviewed with the Bridge release; project callers
cannot add nodes, commands, paths, URLs or Python code.
