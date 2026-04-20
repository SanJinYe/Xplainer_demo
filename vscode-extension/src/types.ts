export type LineRange = [number, number];

export type ApiErrorCategory =
    | "backend_unavailable"
    | "entity_not_found"
    | "timeout"
    | "unknown";

export type SidebarMode = "explain" | "code";
export type HistorySource = "baseline_only" | "mixed" | "traced_only";
export type BackendCodingTaskStatus =
    | "created"
    | "running"
    | "ready_to_apply"
    | "cancelled"
    | "failed"
    | "applied"
    | "applied_event_pending"
    | "applied_without_events";
export type CodingTaskLaunchMode = "new" | "replay";
export type TargetSelectionMode = "follow_active" | "explicit";
export type CodePickerKind = "target" | "context" | "editable";
export type CodingTaskRequestedCapability =
    | "repo_observe"
    | "multi_file"
    | "mcp"
    | "skills";
export type HistoryFilterStatus =
    | "all"
    | "ready_to_apply"
    | "applied"
    | "failed"
    | "cancelled"
    | "applied_event_pending"
    | "applied_without_events";
export type CodeTaskStatus =
    | "idle"
    | "running"
    | "ready_to_apply"
    | "applying"
    | "applied"
    | "error";

export interface BackendParamInfo {
    name: string;
    type_hint?: string | null;
    default?: string | null;
    description?: string | null;
}

export interface BackendEventRef {
    event_id: string;
    role: string;
    timestamp: string;
}

export interface BackendRenameRecord {
    old_qualified_name: string;
    new_qualified_name: string;
    event_id: string;
    timestamp: string;
}

export interface BackendCodeEntity {
    entity_id: string;
    name: string;
    qualified_name: string;
    entity_type: string;
    file_path: string;
    line_range?: LineRange | null;
    signature?: string | null;
    params: BackendParamInfo[];
    return_type?: string | null;
    docstring?: string | null;
    created_at: string;
    created_by_event?: string | null;
    last_modified_event?: string | null;
    last_modified_at?: string | null;
    modification_count: number;
    is_deleted: boolean;
    deleted_by_event?: string | null;
    event_refs: BackendEventRef[];
    rename_history: BackendRenameRecord[];
    is_external: boolean;
    package?: string | null;
    cached_description?: string | null;
    description_valid: boolean;
    in_degree: number;
    out_degree: number;
    tags: string[];
}

export interface BackendEntityRef {
    entity_id: string;
    role: string;
}

export interface BackendExternalRef {
    package: string;
    symbol: string;
    version?: string | null;
    doc_uri?: string | null;
    usage_pattern: string;
}

export interface AuthorizedDocSnapshotPayload {
    file_path: string;
    content: string;
    content_hash: string;
}

export interface DocsSyncRequestPayload {
    documents: AuthorizedDocSnapshotPayload[];
}

export interface DocsSyncSkippedItem {
    file_path: string;
    reason: string;
}

export interface DocsSyncResponsePayload {
    accepted: number;
    skipped: DocsSyncSkippedItem[];
    revision: number;
}

export interface BackendTailEvent {
    event_id: string;
    timestamp: string;
    agent_step_id?: string | null;
    session_id?: string | null;
    action_type: string;
    file_path: string;
    line_range?: LineRange | null;
    code_snapshot: string;
    intent: string;
    reasoning?: string | null;
    decision_alternatives?: string[] | null;
    entity_refs: BackendEntityRef[];
    external_refs: BackendExternalRef[];
}

export interface BaselineOnboardFilePayload {
    file_path: string;
    code_snapshot: string;
}

export interface BaselineOnboardFileResult {
    status: "created" | "skipped";
    file_path: string;
    event_id?: string | null;
    reason?: "duplicate_baseline" | "existing_traced_history" | null;
}

export interface CreateRawEventPayload {
    action_type: "modify";
    file_path: string;
    code_snapshot: string;
    intent: string;
    reasoning?: string | null;
    decision_alternatives?: string[] | null;
    session_id: string;
    agent_step_id?: string | null;
    line_range?: LineRange | null;
    external_refs: BackendExternalRef[];
}

export interface CodingTaskCreateRequestPayload {
    target_file_path: string;
    target_file_version: number;
    user_prompt: string;
    context_files: string[];
    editable_files?: EditableFileReferencePayload[];
    launch_mode?: CodingTaskLaunchMode;
    source_task_id?: string | null;
    selected_profile_id?: string | null;
    requested_capabilities?: CodingTaskRequestedCapability[];
}

export interface CodingTaskCreateResponse {
    task_id: string;
    status: "created";
}

export interface BackendTaskStepEvent {
    task_id: string;
    step_id: string;
    step_kind: "view" | "edit" | "verify";
    status: "started" | "succeeded" | "failed";
    file_path: string;
    content_hash?: string | null;
    intent: string;
    reasoning_summary?: string | null;
    tool_name?: string | null;
    input_summary?: string | null;
    output_summary?: string | null;
    timestamp: string;
}

export interface BackendToolCallPayload {
    task_id: string;
    call_id: string;
    step_id: string;
    tool_name: "view_file";
    file_path: string;
    intent: string;
}

export interface CodingTaskToolResultPayload {
    call_id: string;
    tool_name: "view_file";
    file_path: string;
    document_version?: number | null;
    content?: string | null;
    content_hash?: string | null;
    error?: string | null;
}

export interface CodingTaskDraftResult {
    task_id: string;
    verified_files?: BackendVerifiedFileDraft[];
    updated_file_content?: string | null;
    intent: string;
    reasoning?: string | null;
    session_id: string;
    agent_step_id: string;
    action_type: "modify";
}

export interface CodingTaskAppliedPayload {
    applied_files?: AppliedFileConfirmationPayload[];
    event_id?: string;
}

export interface BackendCodingTaskHistoryItem {
    task_id: string;
    target_file_path: string;
    user_prompt: string;
    status: BackendCodingTaskStatus;
    created_at: string;
    updated_at: string;
}

export interface BackendCodingTaskHistoryPage {
    items: BackendCodingTaskHistoryItem[];
    total: number;
    limit: number;
    offset: number;
    has_more: boolean;
}

export interface BackendCodingTaskHistoryTargetsResponse {
    items: string[];
}

export interface BackendCodingTaskHistoryDetail {
    task_id: string;
    target_file_path: string;
    user_prompt: string;
    context_files: string[];
    editable_files?: string[];
    status: BackendCodingTaskStatus;
    created_at: string;
    updated_at: string;
    steps: BackendTaskStepEvent[];
    model_output_text?: string | null;
    verified_draft_content?: string | null;
    verified_files?: BackendVerifiedFileDraft[];
    intent?: string | null;
    reasoning?: string | null;
    last_error?: string | null;
    applied_event_id?: string | null;
    applied_events?: BackendAppliedEventRecord[];
    launch_mode?: CodingTaskLaunchMode;
    source_task_id?: string | null;
    selected_profile_id?: string | null;
    requested_capabilities?: CodingTaskRequestedCapability[];
}

export interface EditableFileReferencePayload {
    file_path: string;
    document_version: number;
}

export interface AppliedFileConfirmationPayload {
    file_path: string;
    content_hash: string;
}

export interface BackendAppliedEventRecord {
    file_path: string;
    event_id?: string | null;
    status: "pending" | "written" | "failed";
    last_error?: string | null;
}

export interface BackendVerifiedFileDraft {
    file_path: string;
    content: string;
    content_hash: string;
    original_content_hash: string;
    original_document_version?: number | null;
}

export interface CodingProfileSyncItemPayload {
    profile_id: string;
    label: string;
    backend: string;
    model: string;
    is_default: boolean;
    api_key?: string | null;
}

export interface CodingProfilesSyncRequestPayload {
    profiles: CodingProfileSyncItemPayload[];
}

export interface BackendCodingProfileStatusItem {
    profile_id: string;
    label: string;
    backend: string;
    model: string;
    source: "sync" | "env_fallback";
    has_key: boolean;
    is_default: boolean;
    selectable: boolean;
    reason?: string | null;
}

export interface BackendCodingProfilesStatusResponse {
    profiles: BackendCodingProfileStatusItem[];
}

export interface BackendCodingCapabilityState {
    available: boolean;
    reason?: string | null;
}

export interface BackendCodingCapabilitiesResponse {
    repo_observe: BackendCodingCapabilityState;
    multi_file: BackendCodingCapabilityState;
    mcp: BackendCodingCapabilityState;
    skills: BackendCodingCapabilityState;
}

export interface BackendRelatedEntity {
    entity_id: string;
    entity_name: string;
    qualified_name: string;
    entity_type: string;
    direction: string;
    relation_type: string;
    confidence: number;
    context?: string | null;
}

export interface BackendRelationContextItem {
    entity_id: string;
    qualified_name: string;
    kind: "module" | "class" | "function" | "method";
    relation: "caller" | "callee" | "container" | "member";
}

export interface BackendLocalRelationContext {
    callers: BackendRelationContextItem[];
    callees: BackendRelationContextItem[];
    containers: BackendRelationContextItem[];
    members: BackendRelationContextItem[];
}

export interface BackendGlobalRelationContext {
    paths?: BackendGlobalImpactPath[] | null;
    subgraph?: BackendGraphSubgraphSummary | null;
}

export interface BackendRelationContext {
    local: BackendLocalRelationContext;
    global: BackendGlobalRelationContext;
}

export interface BackendEntityExplanation {
    entity_id: string;
    entity_name: string;
    qualified_name: string;
    entity_type: string;
    signature?: string | null;
    resolved_profile_id?: string | null;
    summary: string;
    detailed_explanation?: string | null;
    param_explanations?: Record<string, string> | null;
    return_explanation?: string | null;
    usage_context?: string | null;
    creation_intent?: string | null;
    modification_history: Array<Record<string, unknown>>;
    history_source: HistorySource;
    relation_context: BackendRelationContext;
    related_entities: BackendRelatedEntity[];
    external_doc_snippets: BackendExternalDocMatch[];
    generated_at: string;
    from_cache: boolean;
    confidence: number;
}

export interface BackendGlobalImpactPathStep {
    entity_id: string;
    qualified_name: string;
    entity_type: string;
}

export interface BackendGlobalImpactPath {
    direction: "upstream" | "downstream";
    steps: BackendGlobalImpactPathStep[];
    step_relations?: string[];
    cost: number;
    hop_count: number;
    composed_hops: number;
    terminal_entity_id: string;
    terminal_qualified_name: string;
    terminal_reason?: string;
    evidence_level?: "strong" | "weak";
    truncated: boolean;
    truncation_reason?: string | null;
}

export interface BackendGraphSubgraphSummary {
    depth: number;
    node_count: number;
    edge_count: number;
    truncated: boolean;
    relation_types: string[];
}

export interface BackendExternalDocSource {
    kind: "pydoc" | "workspace_doc";
    package: string;
    symbol: string;
    file_path?: string | null;
    doc_uri?: string | null;
}

export interface BackendExternalDocChunk {
    chunk_id: string;
    content: string;
    content_hash?: string | null;
}

export interface BackendExternalDocMatch {
    source: BackendExternalDocSource;
    chunk: BackendExternalDocChunk;
    usage_pattern: string;
    version?: string | null;
    score: number;
}

export interface BackendExplanationStreamInit {
    event: "init";
    entity_id: string;
    entity_name: string;
    qualified_name: string;
    entity_type: string;
    signature?: string | null;
    resolved_profile_id?: string | null;
    file_path: string;
    line_range?: LineRange | null;
    event_count: number;
    summary?: string | null;
    history_source: HistorySource;
}

export interface BackendExplanationStreamDelta {
    event: "delta";
    text: string;
}

export interface BackendExplanationStreamDone {
    event: "done";
    explanation: BackendEntityExplanation;
}

export interface BackendExplanationStreamError {
    event: "error";
    message: string;
}

export interface ApiSuccess<T> {
    ok: true;
    data: T;
    status: number;
}

export interface ApiFailure {
    ok: false;
    error: ApiErrorCategory;
    status: number | null;
    message?: string;
}

export type ApiResult<T> = ApiSuccess<T> | ApiFailure;

export interface ExplainCommandArgs {
    entityId?: string;
    file?: string;
    line?: number;
}

export interface TimelineItemViewModel {
    eventId: string;
    timestamp: string;
    actionType: string;
    intent: string;
    reasoning?: string | null;
    renameLabel?: string;
}

export interface RelatedEntityViewModel {
    entityId: string;
    label: string;
    relationLabel: string;
    qualifiedName: string;
    direction: string;
}

export interface GlobalImpactPathViewModel {
    direction: "upstream" | "downstream";
    terminalEntityId: string;
    terminalLabel: string;
    qualifiedPath: string;
    costLabel: string;
}

export interface ExternalDocViewModel {
    title: string;
    sourceLabel: string;
    excerpt: string;
}

export interface SidebarViewModel {
    entityId: string;
    entityName: string;
    entityType: string;
    signature?: string | null;
    filePath: string;
    lineStart: number | null;
    lineEnd: number | null;
    eventCount: number;
    summary: string | null;
    summaryPending: boolean;
    historySource: HistorySource;
    disclaimer: string | null;
    detailedExplanation?: string | null;
    streamError?: string | null;
    timeline: TimelineItemViewModel[];
    historyAvailable: boolean;
    historyLoading: boolean;
    callers: RelatedEntityViewModel[];
    callees: RelatedEntityViewModel[];
    relatedEntities: RelatedEntityViewModel[];
    globalImpactPaths: GlobalImpactPathViewModel[];
    globalImpactSummary: string | null;
    globalImpactEmptyText: string;
    externalDocs: ExternalDocViewModel[];
    externalDocsPlaceholder: string;
    profile: EffectiveProfileViewModel | null;
}

export interface CodingTaskHistoryItemViewModel {
    taskId: string;
    targetFilePath: string;
    userPrompt: string;
    status: BackendCodingTaskStatus;
    createdAt: string;
    updatedAt: string;
    selected: boolean;
}

export type DraftFileBaseSource = "workspace_live" | "unavailable";

export interface DraftFileViewModel {
    filePath: string;
    content: string;
    contentHash: string;
    baseContent: string | null;
    baseSource: DraftFileBaseSource;
    originalContentHash: string | null;
    originalDocumentVersion: number | null;
}

export interface RecentTaskSummaryViewModel {
    taskId: string;
    targetFilePath: string;
    userPrompt: string;
    status: BackendCodingTaskStatus;
    updatedAt: string;
}

export interface CodeTaskCardDraftFileViewModel {
    filePath: string;
    content: string;
    baseContent: string | null;
    baseSource: DraftFileBaseSource;
}

export interface CodeTaskCardBase {
    id: string;
    runId: string;
    kind:
        | "run_marker"
        | "user_message"
        | "thinking"
        | "tool_call"
        | "file_change"
        | "verify"
        | "draft_ready"
        | "error";
}

export interface CodeTaskRunMarkerCardViewModel extends CodeTaskCardBase {
    kind: "run_marker";
    targetFilePath: string | null;
    timestamp: string;
    launchMode: CodingTaskLaunchMode;
    sourceTaskId: string | null;
}

export interface CodeTaskUserMessageCardViewModel extends CodeTaskCardBase {
    kind: "user_message";
    prompt: string;
    targetFilePath: string | null;
    contextFiles: string[];
    editableFiles: string[];
}

export interface CodeTaskThinkingCardViewModel extends CodeTaskCardBase {
    kind: "thinking";
    text: string;
    streaming: boolean;
}

export interface CodeTaskToolCallCardViewModel extends CodeTaskCardBase {
    kind: "tool_call";
    stepId: string;
    toolName: string | null;
    filePath: string;
    status: BackendTaskStepEvent["status"];
    summary: string;
}

export interface CodeTaskFileChangeCardViewModel extends CodeTaskCardBase {
    kind: "file_change";
    stepId: string;
    filePath: string;
    status: BackendTaskStepEvent["status"];
    summary: string;
    draftFiles: CodeTaskCardDraftFileViewModel[];
    diffAvailable: boolean;
}

export interface CodeTaskVerifyCardViewModel extends CodeTaskCardBase {
    kind: "verify";
    stepId: string;
    filePath: string;
    status: BackendTaskStepEvent["status"];
    summary: string;
}

export interface AssistantResultFileViewModel {
    filePath: string;
    diffAvailable: boolean;
}

export interface AssistantToolTraceItemViewModel {
    stepId: string;
    stepKind: BackendTaskStepEvent["step_kind"];
    status: BackendTaskStepEvent["status"];
    filePath: string;
    toolName: string | null;
    summary: string;
}

export interface AssistantFileChangeViewModel extends CodeTaskCardDraftFileViewModel {
    summary: string;
    diffAvailable: boolean;
}

export interface AssistantTurnDetails {
    toolTrace: AssistantToolTraceItemViewModel[];
    reasoningSummary: string | null;
    fileChanges: AssistantFileChangeViewModel[];
    verifySummary: string[];
    rawTranscriptSnippet: string | null;
}

export interface MessageActionViewModel {
    type: "open_file" | "open_diff" | "apply" | "dismiss_result";
    label: string;
    path?: string;
    enabled?: boolean;
}

export interface AssistantResultPayload {
    summary: string;
    fileCount: number;
    files: AssistantResultFileViewModel[];
    readyToApply: boolean;
    applied: boolean;
    errorMessage?: string | null;
}

export interface CodeConversationMessageViewModel {
    id: string;
    runId: string;
    kind: "user_turn" | "assistant_working" | "assistant_result" | "assistant_error";
    text: string;
    timestamp: string;
    details?: AssistantTurnDetails | null;
    actions?: MessageActionViewModel[];
}

export interface CodeConversationRunViewModel {
    runId: string;
    sourceTaskId?: string | null;
    targetFilePath: string | null;
    launchMode: CodingTaskLaunchMode;
    status: CodeTaskStatus | "failed" | "cancelled";
    messages: CodeConversationMessageViewModel[];
    result?: AssistantResultPayload;
}

export interface CodeConversationViewModel {
    runs: CodeConversationRunViewModel[];
    composerHintTarget?: string | null;
    composerContextFiles: string[];
    recentTasks: RecentTaskSummaryViewModel[];
}

export interface CodeTaskDraftReadyCardViewModel extends CodeTaskCardBase {
    kind: "draft_ready";
    taskId: string;
    fileCount: number;
    files: AssistantResultFileViewModel[];
    draftFiles: CodeTaskCardDraftFileViewModel[];
    applied: boolean;
}

export interface CodeTaskErrorCardViewModel extends CodeTaskCardBase {
    kind: "error";
    message: string;
}

export type CodeTaskCardViewModel =
    | CodeTaskRunMarkerCardViewModel
    | CodeTaskUserMessageCardViewModel
    | CodeTaskThinkingCardViewModel
    | CodeTaskToolCallCardViewModel
    | CodeTaskFileChangeCardViewModel
    | CodeTaskVerifyCardViewModel
    | CodeTaskDraftReadyCardViewModel
    | CodeTaskErrorCardViewModel;

export interface HistoryDetailStepViewModel {
    stepId: string;
    stepKind: BackendTaskStepEvent["step_kind"];
    status: BackendTaskStepEvent["status"];
    filePath: string;
    summary: string;
    toolName: string | null;
    timestamp: string;
}

export interface CodingTaskHistoryDetailViewModel {
    taskId: string;
    targetFilePath: string;
    userPrompt: string;
    contextFiles: string[];
    editableFiles: string[];
    status: BackendCodingTaskStatus;
    createdAt: string;
    updatedAt: string;
    transcriptText: string;
    modelOutputText: string;
    draftText: string;
    draftFiles: DraftFileViewModel[];
    steps: HistoryDetailStepViewModel[];
    launchMode: CodingTaskLaunchMode;
    sourceTaskId: string | null;
    selectedProfileId: string | null;
    requestedCapabilities: CodingTaskRequestedCapability[];
    appliedEvents: BackendAppliedEventRecord[];
    intent: string | null;
    reasoning: string | null;
    lastError: string | null;
    appliedEventId: string | null;
}

export interface CodePickerCandidateViewModel {
    path: string;
    selected: boolean;
}

export interface CodePickerViewModel {
    open: boolean;
    search: string;
    candidates: CodePickerCandidateViewModel[];
}

export interface CodeExplainEntityViewModel {
    available: boolean;
    entityId: string | null;
    entityName: string | null;
    filePath: string | null;
    canUseAsTarget: boolean;
}

export interface EffectiveProfileViewModel {
    preferenceId: string | null;
    resolvedProfileId: string | null;
    label: string;
    backend: string | null;
    model: string | null;
    source: "sync" | "env_fallback" | null;
    followsCode: boolean;
    available: boolean;
    selectable: boolean;
    reason: string | null;
}

export interface CapabilityBadgeViewModel {
    key: CodingTaskRequestedCapability;
    available: boolean;
    reason?: string | null;
}

export interface CapabilitySummaryViewModel {
    available: CapabilityBadgeViewModel[];
    unavailableCount: number;
}

export interface CodeHistoryFiltersViewModel {
    status: HistoryFilterStatus;
    targetFilePath: string | null;
    targetQuery: string;
    targetSuggestions: string[];
    targetSuggestionsLoading: boolean;
}

export interface CodeHistoryPageViewModel {
    total: number;
    filteredCount: number;
    limit: number;
    offset: number;
    hasMore: boolean;
}

export interface CodeViewModel {
    filePath: string | null;
    targetSelectionMode: TargetSelectionMode;
    contextFiles: string[];
    editableFiles: string[];
    targetPicker: CodePickerViewModel;
    contextPicker: CodePickerViewModel;
    editablePicker: CodePickerViewModel;
    explainEntity: CodeExplainEntityViewModel;
    status: CodeTaskStatus;
    launchMode: CodingTaskLaunchMode;
    sourceTaskId: string | null;
    transcriptText: string;
    modelOutputText: string;
    draftText: string;
    draftFiles: DraftFileViewModel[];
    conversation: CodeConversationViewModel;
    message: string | null;
    canRun: boolean;
    canCancel: boolean;
    canApply: boolean;
    codeProfile: EffectiveProfileViewModel;
    explainProfile: EffectiveProfileViewModel;
    capabilitySummary: CapabilitySummaryViewModel;
    historyLoading: boolean;
    historyError: string | null;
    historyNotice: string | null;
    historyPage: CodeHistoryPageViewModel;
    historyFilters: CodeHistoryFiltersViewModel;
    historyItems: CodingTaskHistoryItemViewModel[];
    historyDetail: CodingTaskHistoryDetailViewModel | null;
}

export interface SidebarEmptyMessage {
    type: "state:empty";
    message: string;
}

export interface SidebarLoadingMessage {
    type: "state:loading";
    label?: string;
}

export interface SidebarUpdateMessage {
    type: "state:update";
    data: SidebarViewModel;
}

export interface SidebarErrorMessage {
    type: "state:error";
    error: ApiErrorCategory;
    baseUrl: string;
}

export interface SidebarModeMessage {
    type: "mode:update";
    mode: SidebarMode;
}

export interface SidebarCodeMessage {
    type: "code:update";
    data: CodeViewModel;
}

export interface SidebarCodeFillPromptMessage {
    type: "code:fillPrompt";
    prompt: string;
}

export type SidebarMessageToWebview =
    | SidebarModeMessage
    | SidebarCodeMessage
    | SidebarCodeFillPromptMessage
    | SidebarEmptyMessage
    | SidebarLoadingMessage
    | SidebarUpdateMessage
    | SidebarErrorMessage;

export interface SidebarReadyMessage {
    type: "ready";
}

export interface SidebarRefreshMessage {
    type: "refresh";
}

export interface SidebarSetModeMessage {
    type: "setMode";
    mode: SidebarMode;
}

export interface SidebarOpenRelatedEntityMessage {
    type: "openRelatedEntity";
    entityId: string;
}

export interface SidebarRunTaskMessage {
    type: "runTask";
    prompt: string;
}

export interface SidebarSetCodePickerOpenMessage {
    type: "setCodePickerOpen";
    kind: CodePickerKind;
    open: boolean;
}

export interface SidebarSetCodePickerSearchMessage {
    type: "setCodePickerSearch";
    kind: CodePickerKind;
    search: string;
}

export interface SidebarSetTargetPickerSelectionMessage {
    type: "setTargetPickerSelection";
    path: string;
}

export interface SidebarUseActiveTargetFileMessage {
    type: "useActiveTargetFile";
}

export interface SidebarUseExplainFileAsTargetMessage {
    type: "useExplainFileAsTarget";
}

export interface SidebarBackToExplainEntityMessage {
    type: "backToExplainEntity";
}

export interface SidebarToggleCodePickerSelectionMessage {
    type: "toggleCodePickerSelection";
    kind: "context" | "editable";
    path: string;
    selected: boolean;
}

export interface SidebarApplyCodePickerSelectionMessage {
    type: "applyCodePickerSelection";
    kind: CodePickerKind;
}

export interface SidebarCancelCodePickerSelectionMessage {
    type: "cancelCodePickerSelection";
    kind: CodePickerKind;
}

export interface SidebarRemoveSelectedFileMessage {
    type: "removeSelectedFile";
    kind: "context" | "editable";
    path: string;
}

export interface SidebarCancelTaskMessage {
    type: "cancelTask";
}

export interface SidebarApplyTaskMessage {
    type: "applyTask";
}

export interface SidebarSelectHistoryTaskMessage {
    type: "selectHistoryTask";
    taskId: string;
}

export interface SidebarReuseHistoryTaskMessage {
    type: "reuseHistoryTask";
    taskId: string;
}

export interface SidebarReplayHistoryTaskMessage {
    type: "replayHistoryTask";
    taskId: string;
}

export interface SidebarSelectCodeProfileMessage {
    type: "selectCodeProfile";
}

export interface SidebarSelectExplainProfileMessage {
    type: "selectExplainProfile";
}

export interface SidebarOnboardRepositoryMessage {
    type: "onboardRepository";
}

export interface SidebarOpenWorkspaceFileMessage {
    type: "openWorkspaceFile";
    path: string;
}

export interface SidebarOpenDiffViewMessage {
    type: "openDiffView";
    path: string;
}

export interface SidebarClearCodeConversationMessage {
    type: "clearCodeConversation";
}

export interface SidebarSetHistoryStatusFilterMessage {
    type: "setHistoryStatusFilter";
    status: HistoryFilterStatus;
}

export interface SidebarSetHistoryTargetQueryMessage {
    type: "setHistoryTargetQuery";
    query: string;
}

export interface SidebarSetHistoryTargetSelectionMessage {
    type: "setHistoryTargetSelection";
    targetFilePath: string | null;
}

export interface SidebarLoadMoreHistoryMessage {
    type: "loadMoreHistory";
}

export type SidebarMessageFromWebview =
    | SidebarReadyMessage
    | SidebarRefreshMessage
    | SidebarSetModeMessage
    | SidebarOpenRelatedEntityMessage
    | SidebarRunTaskMessage
    | SidebarSetCodePickerOpenMessage
    | SidebarSetCodePickerSearchMessage
    | SidebarSetTargetPickerSelectionMessage
    | SidebarUseActiveTargetFileMessage
    | SidebarUseExplainFileAsTargetMessage
    | SidebarBackToExplainEntityMessage
    | SidebarToggleCodePickerSelectionMessage
    | SidebarApplyCodePickerSelectionMessage
    | SidebarCancelCodePickerSelectionMessage
    | SidebarRemoveSelectedFileMessage
    | SidebarCancelTaskMessage
    | SidebarApplyTaskMessage
    | SidebarSelectHistoryTaskMessage
    | SidebarReuseHistoryTaskMessage
    | SidebarReplayHistoryTaskMessage
    | SidebarSelectCodeProfileMessage
    | SidebarSelectExplainProfileMessage
    | SidebarOnboardRepositoryMessage
    | SidebarOpenWorkspaceFileMessage
    | SidebarOpenDiffViewMessage
    | SidebarClearCodeConversationMessage
    | SidebarSetHistoryStatusFilterMessage
    | SidebarSetHistoryTargetQueryMessage
    | SidebarSetHistoryTargetSelectionMessage
    | SidebarLoadMoreHistoryMessage;
