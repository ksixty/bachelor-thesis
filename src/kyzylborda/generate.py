from typing import Dict, Set, List, Any, Optional, Callable, Iterable
import logging
import errno
import sys
import os
import os.path
import subprocess
from tempfile import TemporaryDirectory
import json
import shutil
import uuid
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json

from .utils import get_factory, list_files
from .cache import TasksCache, TaskCache, MultiGeneratorCache
from .db import User, GeneratedTask, GeneratedFlag
from .tasks import Task, TaskName, Flag


logger = logging.getLogger(__name__)


@dataclass_json
@dataclass(frozen=True)
class GeneratedTaskOutput:
    flags: Set[Flag] = field(default_factory=set)
    substitutions: Dict[str, Any] = field(default_factory=dict)
    urls: List[str] = field(default_factory=list)
    bullets: List[str] = field(default_factory=list)


def move_pregenerated(attachments_path: str, task_entries: Dict[TaskName, GeneratedTask], user: User):
    first_task = next(iter(task_entries.values()))
    from_parent_dir = os.path.join(attachments_path, "pregenerated", str(first_task.random_seed))
    to_parent_dir = os.path.join(attachments_path, str(user.id))

    for name, task in task_entries.items():
        from_dir = os.path.join(from_parent_dir, task.task_name)
        to_dir = os.path.join(to_parent_dir, task.task_name)
        try:
            os.rename(from_dir, to_dir)
        except OSError as e:
            # Race condition with another moving operation. Should be fine, just ensure that this is what we expect.
            if e.errno != errno.ENOENT and e.errno != errno.EEXIST:
                raise
    try:
        # Race condition between this and makedirs in generate_task.
        os.rmdir(from_parent_dir)
    except OSError:
        # Directory is not empty.
        pass


def try_use_pregenerated_task(db, attachments_path: str, user: User, tasks: List[TaskName]) -> Optional[Dict[TaskName, GeneratedTask]]:
    initial_task_name = tasks[0]
    while True:
        pregenerated_seed = db.query(GeneratedTask.random_seed).filter_by(user_id=None, task_name=initial_task_name).limit(1).scalar()
        if pregenerated_seed is None:
            break

        # Try and claim some tasks.
        pregenerated_tasks = db.query(GeneratedTask).filter(GeneratedTask.random_seed == pregenerated_seed, GeneratedTask.task_name.in_(tasks)).with_for_update(skip_locked=True).all()
        if len(pregenerated_tasks) < len(tasks):
            if len(pregenerated_tasks) > 0:
                db.rollback()
            continue
        assert len(pregenerated_tasks) == len(tasks)

        for task in pregenerated_tasks:
            task.user_id = user.id
        db.commit()
        is_finished = db.query(GeneratedTask.substitutions.isnot(None)).filter_by(id=pregenerated_tasks[0].id).one_or_none()
        if is_finished is None:
            # Tasks have vanished (failed to generate?).
            continue
        else:
            task_entries = {task.task_name: task for task in pregenerated_tasks}
            if is_finished:
                # Race condition: files may still not be moved when other client gets this "finished" generated entry.
                move_pregenerated(attachments_path, task_entries, user)
            return task_entries
    return None


# Returns set of tasks with files.
def run_task_generator(
        *,
        add_task_result: Callable[[TaskName, GeneratedTaskOutput], None],
        tasks_cache: TasksCache,
        initial_task_cache: TaskCache,
        random_seed: uuid.UUID,
        parent_dir: str
    ):
    initial_task = initial_task_cache.task
    if initial_task.generator is None:
        raise RuntimeError(f"Cannot generate non-dynamic task '{initial_task.name}'")
    if initial_task.generator.multi_generator_key is not None:
        multi_generator: Optional[MultiGeneratorCache] = tasks_cache.multi_generators[initial_task.generator.multi_generator_key]
    else:
        multi_generator = None

    def copy_result(name, from_dir):
        top_dirnames, top_filenames = list_files(from_dir)
        
        for filename in top_filenames:
            logger.warn(f"Generator for '{name}' created a top-level file '{filename}'; ignored")
        for top_dirname in top_dirnames:
            top_dir = os.path.join(from_dir, top_dirname)
            if top_dirname == "attachments":
                dirnames, filenames = list_files(top_dir)
                for dirname in dirnames:
                    logger.warn(f"Generator for '{name}' created a directory '{dirname}' as an attachment; ignored")
                if len(filenames) > 0:
                    result_dir = os.path.join(parent_dir, name, "attachments")
                    os.makedirs(result_dir)
                    for filename in filenames:
                        from_file = os.path.join(top_dir, filename)
                        to_file = os.path.join(result_dir, filename)
                        shutil.move(from_file, to_file)
            elif top_dirname == "static":
                dirnames, filenames = list_files(top_dir)
                if len(filenames) > 0 or len(dirnames) > 0:
                    task_dir = os.path.join(parent_dir, name)
                    os.makedirs(task_dir, exist_ok=True)
                    to_dir = os.path.join(task_dir, top_dirname)
                    shutil.move(top_dir, to_dir)
            else:
                logger.warn(f"Generator for '{name}' created an unknown directory '{top_dirname}'; ignored")


    def convert_task_result(task: Task, raw_output: Dict[TaskName, Any]):
        output = GeneratedTaskOutput.from_dict(raw_output) # type: ignore
        if len(output.flags) == 0 and len(task.flags) == 0:
            raise RuntimeError("Generator didn't return any flags")
        for raw_flag in output.flags:
            flag = raw_flag.lower()
            if flag != raw_flag:
                logger.warn(f"Flag '{raw_flag}' from generator for {task.name} contains uppercase letters; flags are case-insensitive")
            if flag in tasks_cache.static_flags:
                raise RuntimeError(f"Generator for {task.name} returned a flag which clashes with existing static one")
        return output

    with TemporaryDirectory(prefix="kyzylborda_") as root_dir:
        tmpdir = os.path.join(root_dir, "tmp")
        os.mkdir(tmpdir)
        out_dir = os.path.join(root_dir, "out")
        os.mkdir(out_dir)

        if multi_generator is not None:
            for name in multi_generator.tasks:
                from_dir = os.path.join(out_dir, name)
                os.makedirs(os.path.join(from_dir, "attachments"))
                os.makedirs(os.path.join(from_dir, "static"))
        else:
            os.mkdir(os.path.join(out_dir, "attachments"))
            os.mkdir(os.path.join(out_dir, "static"))

        env = os.environ.copy()
        # Don't change HOME; we might need it for Podman or other stuff.
        env["TMPDIR"] = tmpdir
        if multi_generator is not None:
            tasks = ",".join(sorted(multi_generator.tasks))
        else:
            tasks = initial_task.name
        process = subprocess.run(
            initial_task.generator.exec + [str(random_seed), out_dir, tasks],
            cwd=initial_task.generator.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE
        )

        try:
            process.check_returncode()

            raw_output = json.loads(process.stdout)
            if multi_generator is not None:
                outputs = {name: convert_task_result(tasks_cache.tasks[name].task, raw_output[name]) for name in multi_generator.tasks}
            else:
                outputs = {initial_task.name: convert_task_result(initial_task, raw_output)}
        except Exception as e:
            sys.stdout.buffer.write(process.stdout)
            raise

        for name, output in outputs.items():
            add_task_result(name, output)

        if multi_generator is not None:
            for name in multi_generator.tasks:
                from_dir = os.path.join(out_dir, name)
                copy_result(name, from_dir)
        else:
            copy_result(initial_task.name, out_dir)


def generated_tasks_list(tasks_cache: TasksCache, initial_task_cache: TaskCache) -> Set[TaskName]:
    initial_task = initial_task_cache.task
    if initial_task.generator is None:
        raise RuntimeError(f"Task '{initial_task.name}' does not use generators")
    if initial_task.generator.multi_generator_key is not None:
        multi_generator = tasks_cache.multi_generators[initial_task.generator.multi_generator_key]
        return multi_generator.tasks
    else:
        return set([initial_task.name])


def generate_task(db, attachments_path: str, tasks_cache: TasksCache, user: Optional[User], initial_task_cache: TaskCache) -> GeneratedTask:
    initial_task = initial_task_cache.task
    tasks = generated_tasks_list(tasks_cache, initial_task_cache)

    random_seed = uuid.uuid4()

    if user is not None:
        parent_dir = os.path.join(attachments_path, str(user.id))
    else:
        parent_dir = os.path.join(attachments_path, "pregenerated", str(random_seed))
    os.makedirs(parent_dir, exist_ok=True)
    for name in tasks:
        task_dir = os.path.join(parent_dir, name)
        try:
            shutil.rmtree(task_dir)
        except FileNotFoundError:
            pass

    try:
        if user is not None:
            pregen_task_entries = try_use_pregenerated_task(db, attachments_path, user, list(tasks))
            if pregen_task_entries is not None:
                initial_pregenerated = pregen_task_entries[initial_task.name]
                logger.info(f"Using pre-generated tasks {list(pregen_task_entries.keys())} with random seed {initial_pregenerated.random_seed} for user '{user.login}'")
                return initial_pregenerated

        if user is not None:
            user_id = user.id
            user_name = user.login
        else:
            user_id = None
            user_name = "<pregenerated>"

        def make_empty_task(name: TaskName) -> GeneratedTask:
            return GeneratedTask(
                task_name=name,
                user_id=user_id,
                random_seed=random_seed,
                # See db.py for explanation on why don't we just have NULLs for defaults.
                substitutions=None,
                urls=None,
                bullets=None,
            )

        task_entries = {name: make_empty_task(name) for name in tasks}
        for entry in task_entries.values():
            db.add(entry)
        db.commit()

        try:
            logger.info(f"Generating dynamic tasks {list(task_entries.keys())} for user '{user_name}' with random seed '{random_seed}'")
            def add_task_result(name: TaskName, output: GeneratedTaskOutput):
                task_entry = task_entries[name]

                for raw_flag in output.flags:
                    flag = raw_flag.lower()
                    flag_entry = GeneratedFlag(
                        task_id=task_entry.id,
                        flag=flag,
                    )
                    db.add(flag_entry)

                task_entry.substitutions = output.substitutions
                task_entry.urls = output.urls
                task_entry.bullets = output.bullets


            run_task_generator(
                add_task_result=add_task_result,
                tasks_cache=tasks_cache,
                initial_task_cache=initial_task_cache,
                random_seed=random_seed,
                parent_dir=parent_dir,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            for entry in task_entries.values():
                db.delete(entry)
            db.commit()
            raise RuntimeError(f"Generator for {list(task_entries.keys())} failed for user '{user_name}'") from e

        logger.info(f"Successfully generated dynamic tasks {list(task_entries.keys())} for user '{user_name}'")
        initial_entry = task_entries[initial_task.name]

        if user is None:
            claimed_user = db.query(User).join(GeneratedTask).filter_by(id=initial_entry.id).one_or_none()
            if claimed_user is not None:
                move_pregenerated(attachments_path, task_entries, claimed_user)

        return initial_entry
    except:
        for name in tasks:
            task_dir = os.path.join(parent_dir, name)
            try:
                shutil.rmtree(task_dir)
            except FileNotFoundError:
                pass
        raise


def get_or_generate_task(db, attachments_path: str, tasks_cache: TasksCache, user: User, task_cache: TaskCache) -> GeneratedTask:
    generated_task = db.query(GeneratedTask).filter_by(task_name=task_cache.task.name, user_id=user.id).one_or_none()
    if generated_task is not None:
        return generated_task
    else:
        return generate_task(db, attachments_path, tasks_cache, user, task_cache)


def flush_task(db, attachments_path: str, tasks_cache: TasksCache, initial_task_cache: TaskCache):
    initial_task = initial_task_cache.task
    tasks = generated_tasks_list(tasks_cache, initial_task_cache)

    for id, task_name, random_seed, user_id in db.query(GeneratedTask.id, GeneratedTask.task_name, GeneratedTask.random_seed, GeneratedTask.user_id).filter(GeneratedTask.task_name.in_(tasks), GeneratedTask.substitutions.isnot(None)):
        # We delete tasks one by one to avoid inconsistent state (if shutil fails) as much as possible.
        db.query(GeneratedFlag).filter_by(task_id=id).delete()
        db.query(GeneratedTask).filter_by(id=id).delete()
        db.commit()

        if user_id is not None:
            out_dir = os.path.join(attachments_path, str(user_id), task_name)
        else:
            out_dir = os.path.join(attachments_path, "pregenerated", str(random_seed), task_name)

        try:
            shutil.rmtree(out_dir)
        except FileNotFoundError:
            # No attachments exist.
            pass
