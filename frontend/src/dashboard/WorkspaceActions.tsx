import { ArrowUpRight, Camera, ImageUp, ScanLine, Workflow } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

export function WorkspaceActions() {
  const { user } = useAuth();
  const actions = user?.role === "admin"
    ? [
        { title: "标注数据", description: "整理图片、框选目标并发布训练集", to: "/admin/models?view=datasets", icon: ScanLine },
        { title: "训练模型", description: "选择 YOLO 模型，训练并比较效果", to: "/admin/models?view=training", icon: Workflow },
        { title: "识别图片", description: "使用已激活模型，批量验证识别结果", to: "/batch", icon: ImageUp },
      ]
    : [
        { title: "识别图片", description: "上传图片，查看并下载识别结果", to: "/batch", icon: ImageUp },
        { title: "实时监控", description: "查看摄像头画面与现场检测结果", to: "/live", icon: Camera },
      ];
  return (
    <section className="workspace-actions" aria-labelledby="workspace-actions-title">
      <div className="workspace-actions__heading">
        <div><p className="dashboard__eyebrow">BUILD · TRAIN · DETECT</p><h2 id="workspace-actions-title">开始一个视觉任务</h2></div>
        <span>从数据到模型，让每一步都有方向</span>
      </div>
      <nav aria-label="视觉任务快捷入口" className="workspace-actions__grid">
        {actions.map(({ title, description, to, icon: Icon }, index) => (
          <Link key={to} to={to} className="workspace-action">
            <span className="workspace-action__icon"><Icon size={21} aria-hidden="true" /></span>
            <div><small>0{index + 1}</small><strong>{title}</strong><p>{description}</p></div>
            <ArrowUpRight size={18} aria-hidden="true" />
          </Link>
        ))}
      </nav>
    </section>
  );
}
