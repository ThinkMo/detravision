import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { DatasetValidationReport, TrainingDataset } from "../../api/types";
import { ConfirmDialog } from "../shared";
import { DatasetValidationDetails, datasetErrorMessage, validationFromError } from "./DatasetValidationDetails";
import { trainingApi } from "./trainingApi";

type Props = {
  dataset: TrainingDataset;
  completed?: boolean;
  onClose: () => void;
  onPublished: () => void;
  onContinueReview?: () => void;
};

export function DatasetPublishDialog({ dataset, completed, onClose, onPublished, onContinueReview }: Props) {
  const client = useQueryClient();
  const [validation, setValidation] = useState<DatasetValidationReport | null>(null);
  const publish = useMutation({
    mutationFn: async () => {
      // Classes may have changed while this workspace was open. Use the latest
      // dataset revision; the publish endpoint still enforces optimistic locking.
      const latest = (await trainingApi.datasets()).items.find(item => item.id === dataset.id);
      if (!latest) throw new Error("数据集不存在，请返回训练集列表检查。");
      return trainingApi.publishDataset(latest.id, latest.revision, true);
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["admin", "training", "datasets"] });
      void client.invalidateQueries({ queryKey: ["admin", "training", "catalog"] });
      onPublished();
    },
    onError: error => setValidation(validationFromError(error)),
  });
  return <ConfirmDialog title={completed ? "全部标注已完成，是否发布？" : "发布训练集版本"} confirmLabel="校验并发布" pending={publish.isPending} error={publish.isError ? datasetErrorMessage(publish.error, "训练集发布失败，请重试或检查校验结果。") : ""} onCancel={onClose} onConfirm={() => publish.mutate()}>
    {completed ? <p>{dataset.name} · v{dataset.version} 的训练集、验证集和测试集已无待标注图片。可以现在发布，也可以取消后继续检查。</p> : null}
    <p>发布前会再次校验。仍有待标注图片或错误时不会发布；警告项将由本次确认接受。</p>
    {validation ? <DatasetValidationDetails report={validation} /> : null}
    {validation?.errors.length && onContinueReview ? <button type="button" className="secondary-button" onClick={onContinueReview}>继续核验</button> : null}
  </ConfirmDialog>;
}
