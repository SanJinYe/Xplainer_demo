import { strict as assert } from "node:assert";

import {
    buildOnboardingCandidates,
    formatOnboardingSummary,
    MAX_ONBOARD_FILE_BYTES,
    onboardWorkspaceFiles,
    shouldIncludeOnboardingPath,
} from "../src/onboarding";

describe("onboarding helpers", () => {
    it("filters candidate paths using the fixed exclusion rules", () => {
        assert.equal(shouldIncludeOnboardingPath("pkg/demo.py"), true);
        assert.equal(shouldIncludeOnboardingPath("tests/test_demo.py"), true);
        assert.equal(shouldIncludeOnboardingPath(".git/hooks/demo.py"), false);
        assert.equal(shouldIncludeOnboardingPath("pkg/.hidden/demo.py"), false);
        assert.equal(shouldIncludeOnboardingPath("node_modules/demo.py"), false);
        assert.equal(shouldIncludeOnboardingPath("pkg/build/demo.py"), false);
        assert.equal(shouldIncludeOnboardingPath("pkg/dist/demo.py"), false);
        assert.equal(shouldIncludeOnboardingPath("pkg/demo.txt"), false);
        assert.equal(shouldIncludeOnboardingPath("pkg/site.egg-info/demo.py"), false);
    });

    it("builds sorted candidates relative to a workspace root", () => {
        const candidates = buildOnboardingCandidates("C:\\repo\\demo", [
            "C:\\repo\\demo\\tests\\test_demo.py",
            "C:\\repo\\demo\\pkg\\demo.py",
            "C:\\repo\\demo\\.git\\ignored.py",
            "C:\\other\\outside.py",
        ]);

        assert.deepEqual(candidates, [
            {
                absolutePath: "C:\\repo\\demo\\pkg\\demo.py",
                workspaceFilePath: "pkg/demo.py",
            },
            {
                absolutePath: "C:\\repo\\demo\\tests\\test_demo.py",
                workspaceFilePath: "tests/test_demo.py",
            },
        ]);
    });

    it("skips oversized and unreadable files locally before calling the backend", async () => {
        const backendCalls: string[] = [];
        const logs: string[] = [];
        const summary = await onboardWorkspaceFiles({
            apiClient: {
                onboardBaselineFile: async (payload) => {
                    backendCalls.push(payload.file_path);
                    return success({
                        status: "created",
                        file_path: payload.file_path,
                        event_id: "te_1",
                        reason: null,
                    });
                },
            },
            candidates: [
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\large.py",
                    workspaceFilePath: "pkg/large.py",
                },
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\encoded.py",
                    workspaceFilePath: "pkg/encoded.py",
                },
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\ok.py",
                    workspaceFilePath: "pkg/ok.py",
                },
            ],
            readFileBytes: async (absolutePath) => {
                if (absolutePath.endsWith("large.py")) {
                    return new Uint8Array(MAX_ONBOARD_FILE_BYTES + 1);
                }
                if (absolutePath.endsWith("encoded.py")) {
                    return Uint8Array.from([0xff, 0xfe, 0xfd]);
                }
                return new TextEncoder().encode("print(1)\n");
            },
            isCancellationRequested: () => false,
            log: (message) => logs.push(message),
        });

        assert.deepEqual(backendCalls, ["pkg/ok.py"]);
        assert.equal(summary.created, 1);
        assert.equal(summary.skipped, 2);
        assert.equal(summary.failed, 0);
        assert.equal(
            logs.some((message) => message.includes("file_too_large")),
            true,
        );
        assert.equal(
            logs.some((message) => message.includes("unreadable_encoding")),
            true,
        );
    });

    it("stops before dispatching the next file after cancellation", async () => {
        const dispatched: string[] = [];
        let callCount = 0;

        const summary = await onboardWorkspaceFiles({
            apiClient: {
                onboardBaselineFile: async (payload) => {
                    dispatched.push(payload.file_path);
                    callCount += 1;
                    return success({
                        status: "created",
                        file_path: payload.file_path,
                        event_id: `te_${callCount}`,
                        reason: null,
                    });
                },
            },
            candidates: [
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\first.py",
                    workspaceFilePath: "pkg/first.py",
                },
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\second.py",
                    workspaceFilePath: "pkg/second.py",
                },
            ],
            readFileBytes: async () => new TextEncoder().encode("print(1)\n"),
            isCancellationRequested: () => dispatched.length >= 1,
            log: () => undefined,
        });

        assert.deepEqual(dispatched, ["pkg/first.py"]);
        assert.equal(summary.cancelled, true);
        assert.equal(summary.created, 1);
    });

    it("continues after backend failures and reports the final summary", async () => {
        const dispatched: string[] = [];
        const logs: string[] = [];
        const summary = await onboardWorkspaceFiles({
            apiClient: {
                onboardBaselineFile: async (payload) => {
                    dispatched.push(payload.file_path);
                    if (payload.file_path === "pkg/fail.py") {
                        return failure("unknown");
                    }
                    if (payload.file_path === "pkg/skip.py") {
                        return success({
                            status: "skipped",
                            file_path: payload.file_path,
                            event_id: null,
                            reason: "duplicate_baseline",
                        });
                    }
                    return success({
                        status: "created",
                        file_path: payload.file_path,
                        event_id: "te_ok",
                        reason: null,
                    });
                },
            },
            candidates: [
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\fail.py",
                    workspaceFilePath: "pkg/fail.py",
                },
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\skip.py",
                    workspaceFilePath: "pkg/skip.py",
                },
                {
                    absolutePath: "C:\\repo\\demo\\pkg\\ok.py",
                    workspaceFilePath: "pkg/ok.py",
                },
            ],
            readFileBytes: async () => new TextEncoder().encode("print(1)\n"),
            isCancellationRequested: () => false,
            log: (message) => logs.push(message),
        });

        assert.deepEqual(dispatched, ["pkg/fail.py", "pkg/skip.py", "pkg/ok.py"]);
        assert.equal(summary.created, 1);
        assert.equal(summary.skipped, 1);
        assert.equal(summary.failed, 1);
        assert.equal(
            formatOnboardingSummary(summary),
            "TailEvents onboarding finished: 1 created, 1 skipped, 1 failed.",
        );
        assert.equal(
            logs.some((message) => message.includes("Onboard failed (unknown): pkg/fail.py")),
            true,
        );
    });
});

function success<T>(data: T) {
    return {
        ok: true as const,
        data,
        status: 200,
    };
}

function failure(error: "unknown") {
    return {
        ok: false as const,
        error,
        status: 500,
        message: undefined,
    };
}
