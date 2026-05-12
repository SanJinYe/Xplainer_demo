import assert from "node:assert/strict";

import {
    createInitialState,
    reducer,
} from "../webview-ui/src/state/reducer";
import type { SidebarViewModel } from "../src/types";

describe("webview reducer", () => {
    it("keeps review hints in explain state updates", () => {
        const data: SidebarViewModel = {
            entityId: "entity-1",
            entityName: "target",
            entityType: "function",
            signature: "def target(): ...",
            filePath: "target.py",
            lineStart: 1,
            lineEnd: 2,
            eventCount: 1,
            summary: "Cline modify target.py",
            summaryPending: false,
            historySource: "traced_only",
            disclaimer: null,
            detailedExplanation: null,
            streamError: null,
            timeline: [],
            historyAvailable: true,
            historyLoading: false,
            callers: [],
            callees: [],
            relatedEntities: [],
            globalImpactPaths: [],
            globalImpactSummary: null,
            globalImpactEmptyText: "No global paths yet.",
            reviewHints: [
                {
                    id: "review.timeline",
                    category: "review",
                    severity: "success",
                    title: "Cline trace linked",
                    body: "1 event is linked.",
                },
            ],
            externalDocs: [],
            externalDocsPlaceholder: "No docs yet.",
            profile: null,
        };

        const state = reducer(createInitialState(), {
            type: "host/message",
            message: {
                type: "state:update",
                data,
            },
        });

        assert.equal(state.explainState.type, "state:update");
        if (state.explainState.type === "state:update") {
            assert.equal(state.explainState.data.reviewHints[0].title, "Cline trace linked");
        }
    });
});
