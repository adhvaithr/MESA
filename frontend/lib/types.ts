export interface CallMeta {
  callId: string;
  startedAt: string;
  agent: string;
  callerNumber: string;
  totalDuration: number;
  channel: string;
  intent: string;
  language: string;
  region: string;
}

export interface Write {
  block: string;
  field: string;
  value: unknown;
}

export interface TurnEvent {
  t: number;
  kind: 'turn';
  speaker: 'agent' | 'caller';
  text: string;
}

export interface ToolEvent {
  t: number;
  kind: 'tool';
  name: string;
  args: Record<string, unknown>;
  result: Record<string, unknown>;
  durationMs: number;
  writes: Write[];
}

export interface ClassifyEvent {
  t: number;
  kind: 'classify';
  callerType: string;
  confidence: number;
  rationale: string;
  writes: Write[];
}

export type TimelineEvent = TurnEvent | ToolEvent | ClassifyEvent;

export interface BlockFieldCell {
  value: unknown;
  writtenAt: number;
  tool: string;
}

export interface BlockState {
  [field: string]: BlockFieldCell;
}

export interface AppState {
  state: Record<string, BlockState>;
  toolEvents: ToolEvent[];
  callerType: { id: string; confidence: number; rationale: string; classifiedAt: number } | null;
}

export interface BlockDef {
  id: string;
  title: string;
  subtitle: string;
  icon: string;
  fields: string[];
  listField?: string;
}

export interface CallerTypeDef {
  id: string;
  label: string;
  desc: string;
  icon: string;
}

export interface CallListItem {
  id: string;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  caller_number: string | null;
  agent_name: string;
  tool_call_count: number;
  intent: string | null;
}

export interface TweakValues {
  theme: 'light' | 'dark';
  density: 'comfortable' | 'compact';
  pulseIntensity: 'none' | 'subtle' | 'strong';
  autoExpand: boolean;
  showInlineTools: boolean;
  playbackSpeed: number;
}
