export type LineRange = [number, number];

export type ApiErrorCategory =
    | "backend_unavailable"
    | "entity_not_found"
    | "timeout"
    | "unknown";

export type SidebarMode = "explain" | "code";
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

export interface CreateRawEventPayload {
    action_type: "create" | "modify";
    file_path: string;
    code_snapshot: string;
    intent: string;
    reasoning?: string | null;
    decision_alternatives?: string[] | null;
    session_id: string;
    line_range?: LineRange | null;
    external_refs: BackendExternalRef[];
}

export interface CodingTaskRequestPayload {
    file_path: string;
    file_content: string;
    user_prompt: string;
}

export interface CodingTaskEdit {
    old_text: string;
    new_text: string;
}

export interface CodingTaskResult {
    updated_file_content: string;
    edits: CodingTaskEdit[];
    intent: string;
    reasoning?: string | null;
    action_type: "create" | "modify";
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

export interface BackendEntityExplanation {
    entity_id: string;
    entity_name: string;
    qualified_name: string;
    entity_type: string;
    signature?: string | null;
    summary: string;
    detailed_explanation?: string | null;
    param_explanations?: Record<string, string> | null;
    return_explanation?: string | null;
    usage_context?: string | null;
    creation_intent?: string | null;
    modification_history: Array<Record<string, unknown>>;
    related_entities: BackendRelatedEntity[];
    external_doc_snippets: Array<Record<string, unknown>>;
    generated_at: string;
    from_cache: boolean;
    confidence: number;
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

export interface SidebarViewModel {
    entityId: string;
    entityName: string;
    entityType: string;
    signature?: string | null;
    filePath: string;
    lineStart: number | null;
    lineEnd: number | null;
    eventCount: number;
    summary: string;
    detailedExplanation?: string | null;
    timeline: TimelineItemViewModel[];
    historyAvailable: boolean;
    relatedEntities: RelatedEntityViewModel[];
}

export interface CodeViewModel {
    filePath: string | null;
    status: CodeTaskStatus;
    streamedText: string;
    message: string | null;
    canRun: boolean;
    canCancel: boolean;
    canApply: boolean;
    canRetryEventWrite: boolean;
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

export type SidebarMessageToWebview =
    | SidebarModeMessage
    | SidebarCodeMessage
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

export interface SidebarCancelTaskMessage {
    type: "cancelTask";
}

export interface SidebarApplyTaskMessage {
    type: "applyTask";
}

export interface SidebarRetryEventWriteMessage {
    type: "retryEventWrite";
}

export type SidebarMessageFromWebview =
    | SidebarReadyMessage
    | SidebarRefreshMessage
    | SidebarSetModeMessage
    | SidebarOpenRelatedEntityMessage
    | SidebarRunTaskMessage
    | SidebarCancelTaskMessage
    | SidebarApplyTaskMessage
    | SidebarRetryEventWriteMessage;
