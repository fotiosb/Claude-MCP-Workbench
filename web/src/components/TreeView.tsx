import type { Tree } from "../types";

export function TreeView({ tree }: { tree: Tree }) {
  const files = (tree.files || []).slice(0, 200);
  return (
    <div className="tree">
      <p>
        {tree.file_count} files, {tree.dir_count} directories
        {tree.top_level_only ? " (top-level only)" : ""}.
      </p>
      <ul>
        {files.map((f) => (
          <li key={f.path}>
            <code>{f.path}</code>
            <span style={{ color: "#5c6270" }}>
              {" "}
              {f.size} B{f.binary ? " bin" : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
