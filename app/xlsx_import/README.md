# XLSX import worker

Start the API and the import worker as separate processes from the backend
directory:

```powershell
uvicorn app.main:app --reload
python -m app.xlsx_import.worker
```

The API stores each uploaded file in `XLSX_UPLOAD_DIR` and records a queued job
in `dataset_versions`. The worker polls that table, imports queued files, and
updates the existing dataset status and progress fields. With MySQL's bulk-load
path, status changes to `loading` while the staged CSV is being loaded into
MySQL, so that phase is distinguishable from Excel parsing. Run only one worker;
it acquires a MySQL advisory lock to prevent duplicate workers from processing
the same queue.

The API and worker must use the same database and upload directory. If they run
on different machines or containers, mount a shared persistent directory at the
same `XLSX_UPLOAD_DIR` path in both. The default upload directory is a
machine-local temporary directory and is suitable only when both processes run
on the same machine.

Jobs interrupted while importing are returned to the queue when the worker
restarts. The worker recreates their temporary import table and retries them.
Jobs created by the previous in-process implementation cannot be resumed if
their process has already ended; those are marked failed and must be uploaded
again.
