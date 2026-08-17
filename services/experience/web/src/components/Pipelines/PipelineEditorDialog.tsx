import { useEffect, useId, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { DndContext, type DragEndEvent, closestCenter } from "@dnd-kit/core";
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button, FormGroup, Icon, InputGroup } from "@blueprintjs/core";
import { useCreatePipeline, useDatasets } from "../../api/hooks";
import type { PipelineDefinition, TransformStep } from "../../api/connectivity";
import { RegistryDialog } from "../common/RegistryDialog";
import { useAsyncAction } from "../../hooks/useAsyncAction";

type StepForm = {
  id: string;
  step_name: string;
  input_dataset: string;
  function_name: string;
  output_dataset: string;
  value_type_casts_json: string;
};

function newStepId(): string {
  return crypto.randomUUID();
}

function emptyStep(): StepForm {
  return {
    id: newStepId(),
    step_name: "",
    input_dataset: "",
    function_name: "",
    output_dataset: "",
    value_type_casts_json: "",
  };
}

function stepsToForm(steps: TransformStep[] | undefined): StepForm[] {
  if (!steps || steps.length === 0) return [emptyStep()];
  return steps.map((s) => ({
    id: newStepId(),
    step_name: s.step_name ?? "",
    input_dataset: s.input_dataset ?? "",
    function_name: s.function_name ?? "",
    output_dataset: s.output_dataset ?? "",
    value_type_casts_json: s.value_type_casts ? JSON.stringify(s.value_type_casts) : "",
  }));
}

function SortableStepCard({
  step,
  index,
  datalistId,
  canRemove,
  onChange,
  onRemove,
}: {
  step: StepForm;
  index: number;
  datalistId: string;
  canRemove: boolean;
  onChange: (patch: Partial<StepForm>) => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: step.id });

  return (
    <div
      ref={setNodeRef}
      className="hl-pipeline-create-step"
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 }}
    >
      <div className="hl-flex-between hl-mb-xs">
        <div className="hl-flex-row hl-items-center hl-gap-xs">
          <span {...listeners} {...attributes} className="hl-drag-handle" title="Drag to reorder">
            <Icon icon="drag-handle-vertical" />
          </span>
          <span className="hl-text-muted-sm">Step {index + 1}</span>
        </div>
        <Button minimal small icon="cross" disabled={!canRemove} onClick={onRemove} />
      </div>
      <div className="hl-pipeline-create-grid">
        <FormGroup label="Step name" className="hl-mb-0">
          <InputGroup
            value={step.step_name}
            onChange={(e) => onChange({ step_name: e.target.value })}
            placeholder="flag-high-value"
          />
        </FormGroup>
        <FormGroup label="Function" className="hl-mb-0" helperText="Registered Knowledge Function">
          <InputGroup
            value={step.function_name}
            onChange={(e) => onChange({ function_name: e.target.value })}
            placeholder="flag_high_value_order"
          />
        </FormGroup>
        <FormGroup label="Input dataset" className="hl-mb-0">
          <InputGroup
            value={step.input_dataset}
            onChange={(e) => onChange({ input_dataset: e.target.value })}
            placeholder="orders"
            list={datalistId}
          />
        </FormGroup>
        <FormGroup label="Output dataset" className="hl-mb-0">
          <InputGroup
            value={step.output_dataset}
            onChange={(e) => onChange({ output_dataset: e.target.value })}
            placeholder="orders_hv"
          />
        </FormGroup>
        <FormGroup
          label="Value type casts (optional)"
          className="hl-mb-0"
          helperText='JSON map column → Value Type, e.g. {"email":"Email"}'
        >
          <InputGroup
            className="hl-mono"
            value={step.value_type_casts_json}
            onChange={(e) => onChange({ value_type_casts_json: e.target.value })}
            placeholder='{"status":"OrderStatus"}'
          />
        </FormGroup>
      </div>
    </div>
  );
}

export function PipelineEditorDialog({
  isOpen,
  onClose,
  pipeline,
}: {
  isOpen: boolean;
  onClose: () => void;
  /** When set, dialog edits that pipeline (name locked). */
  pipeline?: PipelineDefinition | null;
}) {
  const navigate = useNavigate();
  const savePipeline = useCreatePipeline();
  const { data: datasets = [] } = useDatasets();
  const editing = !!pipeline;
  const [name, setName] = useState("");
  const [steps, setSteps] = useState<StepForm[]>([emptyStep()]);
  const datalistId = `hl-pipeline-dataset-suggestions-${useId().replace(/:/g, "")}`;

  const datasetSuggestions = datasets.map((d) => d.display_name);
  const stepIds = steps.map((s) => s.id);

  useEffect(() => {
    if (!isOpen) return;
    if (pipeline) {
      setName(pipeline.name);
      setSteps(stepsToForm(pipeline.steps));
    } else {
      setName("");
      setSteps([emptyStep()]);
    }
  }, [isOpen, pipeline]);

  function close() {
    onClose();
  }

  function updateStep(id: string, patch: Partial<StepForm>) {
    setSteps((prev) => prev.map((step) => (step.id === id ? { ...step, ...patch } : step)));
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    setSteps((prev) => {
      const oldIndex = prev.findIndex((s) => s.id === active.id);
      const newIndex = prev.findIndex((s) => s.id === over.id);
      if (oldIndex < 0 || newIndex < 0) return prev;
      return arrayMove(prev, oldIndex, newIndex);
    });
  }

  const ready =
    !!name.trim() &&
    steps.length > 0 &&
    steps.every(
      (s) => s.step_name.trim() && s.input_dataset.trim() && s.function_name.trim() && s.output_dataset.trim(),
    );

  const { submit, error, isPending } = useAsyncAction(async () => {
    const pipelineName = name.trim();
    const bodySteps: TransformStep[] = steps.map((s) => {
      const step: TransformStep = {
        step_name: s.step_name.trim(),
        input_dataset: s.input_dataset.trim(),
        function_name: s.function_name.trim(),
        output_dataset: s.output_dataset.trim(),
      };
      const raw = s.value_type_casts_json.trim();
      if (raw) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          throw new Error(`Step "${s.step_name}": value type casts must be valid JSON`);
        }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || Object.keys(parsed as object).length === 0) {
          throw new Error(`Step "${s.step_name}": value type casts must be a non-empty object`);
        }
        step.value_type_casts = parsed as Record<string, string>;
      }
      return step;
    });
    await savePipeline.mutateAsync({ name: pipelineName, steps: bodySteps });
    close();
    if (!editing) {
      void navigate({ to: "/pipelines/$name", params: { name: pipelineName } });
    }
  }, {
    successMessage: editing
      ? `Pipeline "${name.trim()}" saved`
      : `Pipeline "${name.trim()}" created`,
  });

  return (
    <RegistryDialog
      isOpen={isOpen}
      title={editing ? `Edit ${pipeline?.name ?? "pipeline"}` : "New pipeline"}
      onClose={close}
      error={error}
      isPending={isPending}
      submitLabel={editing ? "Save" : "Create"}
      submitDisabled={!ready}
      onSubmit={() => submit(undefined)}
      style={{ width: 640 }}
    >
      <p className="hl-text-muted-sm hl-mb-md">
        Steps run in the listed order — drag the handle to reorder. An input may be a catalogued dataset or an
        earlier step&apos;s output; forward references are rejected at save time.
      </p>
      <FormGroup label="Name" helperText={editing ? "Name is immutable — create a new pipeline to rename." : undefined}>
        <InputGroup
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="flag-high-value-orders"
          disabled={editing}
          autoFocus={!editing}
        />
      </FormGroup>

      <datalist id={datalistId}>
        {datasetSuggestions.map((d) => (
          <option key={d} value={d} />
        ))}
        {steps
          .map((s) => s.output_dataset.trim())
          .filter(Boolean)
          .map((d) => (
            <option key={`out-${d}`} value={d} />
          ))}
      </datalist>

      <FormGroup label="Steps">
        <DndContext collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
          <SortableContext items={stepIds} strategy={verticalListSortingStrategy}>
            <div className="hl-flex-col hl-gap-sm">
              {steps.map((step, index) => (
                <SortableStepCard
                  key={step.id}
                  step={step}
                  index={index}
                  datalistId={datalistId}
                  canRemove={steps.length > 1}
                  onChange={(patch) => updateStep(step.id, patch)}
                  onRemove={() => setSteps((prev) => prev.filter((s) => s.id !== step.id))}
                />
              ))}
              <Button small minimal icon="plus" onClick={() => setSteps((prev) => [...prev, emptyStep()])}>
                Add step
              </Button>
            </div>
          </SortableContext>
        </DndContext>
      </FormGroup>
    </RegistryDialog>
  );
}
