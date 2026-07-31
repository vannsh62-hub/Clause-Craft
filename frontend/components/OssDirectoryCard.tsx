import { OssProject, languageColor } from "@/lib/ossDirectory";

function formatCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
  return String(n);
}

export function OssDirectoryCard({ project }: { project: OssProject }) {
  return (
    <a
      className="oss-card"
      href={`https://github.com/${project.repo}`}
      target="_blank"
      rel="noreferrer"
    >
      <div className="oss-card-categories">
        {project.categories.map((c) => (
          <span key={c} className="oss-tag">
            {c}
          </span>
        ))}
      </div>
      <h3 className="oss-card-title">{project.name}</h3>
      <p className="oss-card-repo">{project.repo}</p>
      <p className="oss-card-desc">{project.description}</p>
      <div className="oss-card-meta">
        <span className="oss-card-meta-item">★ {formatCount(project.stars)}</span>
        <span className="oss-card-meta-item">⑂ {formatCount(project.forks)}</span>
        <span className="oss-card-meta-item">{project.updatedAgo}</span>
        {project.language && (
          <span className="oss-card-meta-item">
            <span
              className="oss-card-lang-dot"
              style={{ background: languageColor(project.language) }}
            />
            {project.language}
          </span>
        )}
      </div>
    </a>
  );
}