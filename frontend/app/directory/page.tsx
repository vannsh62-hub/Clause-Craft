import { ossProjects } from "@/lib/ossDirectory";
import { OssDirectoryCard } from "@/components/OssDirectoryCard";

export const metadata = {
  title: "Open Source Legal Software Directory",
};

export default function DirectoryPage() {
  return (
    <div className="oss-directory">
      <div className="oss-header">
        <div>
          <h1 className="oss-title">Open Source Legal Software.</h1>
          <p className="oss-subtitle">
            Every project is a real GitHub repository, stats refreshed from the source. One
            listing per repo, reviewed by the community, claimed by its maintainer.
          </p>
        </div>
        <span className="oss-tracked">136.5k tracked</span>
      </div>
      <p className="oss-count">{ossProjects.length} projects indexed</p>
      <div className="oss-grid">
        {ossProjects.map((project) => (
          <OssDirectoryCard key={project.slug} project={project} />
        ))}
      </div>
    </div>
  );
}