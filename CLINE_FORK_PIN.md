# Cline fork

- repo: github.com/SanJinYe/cline
- local path: C:\Users\16089\ref-agent\cline
- upstream: github.com/cline/cline
- upstream baseline: main@5fe6c9a8c, Cline 3.81.0
- work branch: cline-tailevents-bridge-20260426
- pinned commit: 68b61d506
- why we forked: upstream Cline does not expose TailEvents-compatible
  task trace events. We need a thin bridge inside the Cline task message
  loop to emit native Cline messages to TailEvents.
- changes:
  - added `src/integrations/tailevents/TraceBridge.ts`
  - attached the bridge after `MessageStateHandler` is created in
    `src/core/task/index.ts`
  - added `cline.tailEvents.enabled` and `cline.tailEvents.apiBaseUrl`
    settings in `package.json`
  - added focused TraceBridge tests under
    `src/integrations/tailevents/__tests__/`
- ownership: TailEvents is the protocol owner repo. This Cline fork is a
  managed external repo and should not contain TailEvents protocol files.
- sync plan: periodically rebase this branch on `cline/cline/main`, then
  push to `SanJinYe/cline`.
- merge-back plan: not planned; this bridge is TailEvents-specific.
- TailEvents counterpart: github.com/SanJinYe/Xplainer_demo,
  branch `cline-tailevents-bridge-20260426`, commit `618a65d`.
