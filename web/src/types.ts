export type Example = {
  id: string;
  owner: string;
  title: string;
  url: string;
  blurb: string;
  size: string;
  image: string;
  cache_hot?: boolean;
};

export type Classify = {
  kind: string;
  skill: string | null;
  reason: string;
  accepted: boolean;
  owner?: string;
  repo?: string;
  branch?: string | null;
  normalized_url?: string;
};

export type FindingSecret = {
  kind: string;
  severity: string;
  path: string;
  detail: string;
  sample?: string;
};

export type Manifest = { path: string; name: string; [k: string]: unknown };
export type Workflow = { path: string; jobs?: string[]; actions?: string[] };
export type Tests = {
  present: boolean;
  test_file_count: number;
  test_dirs: string[];
  frameworks_guess: string[];
};

export type TreeFile = { path: string; size: number; binary?: boolean };
export type Tree = {
  files: TreeFile[];
  dirs: string[];
  file_count: number;
  dir_count: number;
  top_level_only?: boolean;
};

export type Findings = {
  manifests?: Manifest[];
  ci?: Workflow[];
  secrets?: FindingSecret[];
  tests?: Tests;
};

export type InputRequest = {
  id: string;
  title: string;
  message: string;
  choices: { id: string; label: string }[];
};

export type Run = {
  id: string;
  url: string;
  normalized_url?: string;
  owner?: string;
  repo?: string;
  branch?: string;
  sha?: string;
  status: string;
  stage?: string;
  progress_message?: string;
  progress?: {
    stage?: string | null;
    detail?: string | null;
    elapsed_ms?: number | null;
    files_seen?: number | null;
    files_total?: number | null;
    bytes_seen?: number | null;
  };
  error?: string | null;
  cache_hit?: boolean;
  findings?: Findings | null;
  tree?: Tree | null;
  brief?: string;
  brief_source?: string;
  resource_uri?: string;
  input_request?: InputRequest | null;
};

export type ArchEvent = {
  id: number;
  run_id: string | null;
  stage: string;
  message: string;
  created_at: number;
};

export type Architecture = {
  title: string;
  deck: string;
  mcp_endpoint: string;
  protocol: string;
  mcp_mode: string;
  tasks: string;
  tools?: string[];
  tool_count?: number;
  nodes: { id: string; label: string }[];
  edges: [string, string][];
  stages: string[];
  inspector: ArchEvent[];
};

export type SettingsStatus = {
  password_configured: boolean;
  authenticated: boolean;
  llm_configured: boolean;
  model: string;
  effort: string;
  key_suffix: string | null;
};
