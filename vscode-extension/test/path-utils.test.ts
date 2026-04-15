import { strict as assert } from "node:assert";

import {
    getFileLookupCandidates,
    getWorkspaceRelativePathCandidates,
    toWorkspaceRelativePath,
} from "../src/path-utils";

describe("toWorkspaceRelativePath", () => {
    it("returns a normalized relative path for files inside the workspace", () => {
        const result = toWorkspaceRelativePath("C:\\repo\\demo\\pkg\\module.py", [
            createWorkspaceFolder("C:\\repo\\demo"),
        ]);

        assert.equal(result, "pkg/module.py");
    });

    it("prefers the longest matching workspace root", () => {
        const result = toWorkspaceRelativePath("C:\\repo\\demo\\nested\\pkg\\module.py", [
            createWorkspaceFolder("C:\\repo\\demo"),
            createWorkspaceFolder("C:\\repo\\demo\\nested"),
        ]);

        assert.equal(result, "pkg/module.py");
    });

    it("returns null for files outside all workspaces", () => {
        const result = toWorkspaceRelativePath("C:\\other\\module.py", [
            createWorkspaceFolder("C:\\repo\\demo"),
        ]);

        assert.equal(result, null);
    });

    it("normalizes backslashes to forward slashes", () => {
        const result = toWorkspaceRelativePath("C:\\repo\\demo\\sub\\file.py", [
            createWorkspaceFolder("C:\\repo\\demo"),
        ]);

        assert.equal(result, "sub/file.py");
    });

    it("returns null when no workspace folders are available", () => {
        const result = toWorkspaceRelativePath("C:\\repo\\demo\\pkg\\module.py", []);

        assert.equal(result, null);
    });

    it("returns null when the target path equals the workspace root", () => {
        const result = toWorkspaceRelativePath("C:\\repo\\demo", [
            createWorkspaceFolder("C:\\repo\\demo"),
        ]);

        assert.equal(result, null);
    });

    it("returns null when the path is missing or not absolute", () => {
        assert.equal(toWorkspaceRelativePath("", [createWorkspaceFolder("C:\\repo\\demo")]), null);
        assert.equal(toWorkspaceRelativePath("relative.py", [createWorkspaceFolder("C:\\repo\\demo")]), null);
    });

    it("includes a workspace-folder-prefixed fallback candidate", () => {
        const result = getWorkspaceRelativePathCandidates("C:\\repo\\demo\\vscode-extension\\manual_test_target.py", [
            createWorkspaceFolder("C:\\repo\\demo\\vscode-extension"),
        ]);

        assert.deepEqual(result, [
            "manual_test_target.py",
            "vscode-extension/manual_test_target.py",
        ]);
    });

    it("falls back to absolute-path suffixes when no workspace folders are available", () => {
        const result = getFileLookupCandidates(
            "C:\\Users\\16089\\demo\\vscode-extension\\manual_test_target.py",
            undefined,
        );

        assert.deepEqual(result.slice(0, 4), [
            "manual_test_target.py",
            "vscode-extension/manual_test_target.py",
            "demo/vscode-extension/manual_test_target.py",
            "16089/demo/vscode-extension/manual_test_target.py",
        ]);
    });
});

function createWorkspaceFolder(fsPath: string) {
    return {
        index: 0,
        name: fsPath.split("\\").slice(-1)[0],
        uri: {
            fsPath,
        },
    } as any;
}
