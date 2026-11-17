"""Execute the revised notebook in a fresh kernel; save outputs only on success."""
import json
import os
from pathlib import Path
import tempfile

import nbformat
from nbclient import NotebookClient


def main():
    root = Path(__file__).resolve().parents[1]
    path = root / "revised_analysis_observed_baseline.ipynb"
    with tempfile.TemporaryDirectory(prefix="energy-notebook-") as temporary:
        for variable, directory in [("MPLCONFIGDIR", "matplotlib"),
                                    ("IPYTHONDIR", "ipython"),
                                    ("JUPYTER_RUNTIME_DIR", "jupyter")]:
            target = Path(temporary) / directory
            target.mkdir()
            os.environ[variable] = str(target)
        os.environ["MPLBACKEND"] = "Agg"
        notebook = nbformat.read(path, as_version=4)
        def completed(cell_index, **kwargs):
            print(f"Completed notebook code cell at index {cell_index}", flush=True)
        client = NotebookClient(notebook, timeout=1200, kernel_name="python3",
                                resources={"metadata": {"path": str(root)}},
                                on_cell_executed=completed)
        client.execute()
        nbformat.write(notebook, path)
    cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
    errors = [output for cell in cells for output in cell.get("outputs", [])
              if output.output_type == "error"]
    result = dict(notebook=path.name, fresh_kernel=True, code_cells=len(cells),
                  execution_counts=[cell.execution_count for cell in cells],
                  error_outputs=len(errors), all_code_cells_executed=all(c.execution_count for c in cells))
    if errors or not result["all_code_cells_executed"]:
        raise RuntimeError("Notebook did not complete every code cell")
    (root / "reports/revised_analysis/notebook_execution.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
