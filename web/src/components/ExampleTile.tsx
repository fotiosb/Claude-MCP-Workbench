import type { Example } from "../types";

export function ExampleTile({ example, onPick }: { example: Example; onPick: (url: string) => void }) {
  return (
    <button type="button" className="tile" onClick={() => onPick(example.url)}>
      <img src={example.image} alt="" />
      <div className="body">
        <h3>{example.title}</h3>
        <p>{example.blurb}</p>
        <div className="meta">
          {example.owner} · {example.size}
          {example.cache_hot ? <span className="hot"> · cache warm</span> : null}
        </div>
      </div>
    </button>
  );
}
