"""LLM prompts used by the pipeline-generator agent."""

PIPELINE_SYSTEM_PROMPT = """You are a senior DevOps engineer. You generate production-ready CI/CD pipeline \
configuration files. Output ONLY valid YAML — no markdown fences, no explanations, no extra text. \
The YAML must be syntactically correct and follow the exact schema of the target CI/CD platform.

CRITICAL RULES:
- Use the EXACT dependency file paths provided. Do NOT assume files are in the root directory.
- If a requirements.txt is at "backend/requirements.txt", use "backend/requirements.txt" in the pipeline.
- If manage.py is at "myapp/manage.py", run commands from that directory.
- Always cd into the correct directory or use working-directory options when files are in subdirectories.
- NEVER hardcode branch names like 'main', 'master', or 'develop' in triggers. Use ONLY the branch name provided in the prompt."""

CI_PIPELINE_PROMPT = """Generate a CI (Continuous Integration) pipeline for {platform}.

Repository analysis:
- Languages: {languages}
- Frameworks: {frameworks}
- Package managers: {package_managers}
- Architecture: {architecture}
- Docker: has_dockerfile={has_dockerfile}, base_images={base_images}
- Test frameworks: {test_frameworks}
- Test directories: {test_directories}

Dependency file locations (EXACT paths — use these, do NOT assume root):
{dependency_file_details}

Requirements:
1. Install dependencies using the EXACT file paths listed above.
2. Run linting / static checks appropriate for the detected languages and frameworks.
3. Run the test suite using the detected test frameworks.
4. Build the application (compile, bundle, or docker build as appropriate).
5. Use caching for dependencies where supported.
6. Trigger on push to ALL branches — do NOT add a `branches:` filter under `push:`.
   For `pull_request:`, filter to the '{default_branch}' branch ONLY.
   CRITICAL: '{default_branch}' is the EXACT, LITERAL branch name — not a description.
   Example trigger block:
     on:
       push:
       pull_request:
         branches: ['{default_branch}']
7. Use the correct test command for the detected framework (e.g. pytest, npm test,
   mvn test, go test, dotnet test, manage.py test for Django, etc.).
   Run commands from the correct working directory based on the dependency file paths.

Output ONLY the YAML content for the pipeline file.
"""

COVERAGE_JOB_PROMPT = """You are editing an existing CI pipeline for {platform}.
Add a **coverage** job to the workflow below. The coverage job MUST depend on the
existing test/build job (use `needs:`).

### Current CI pipeline YAML:
```yaml
{current_ci_yaml}
```

Repository analysis:
- Languages: {languages}
- Frameworks: {frameworks}
- Test frameworks: {test_frameworks}
- Test directories: {test_directories}
- Has existing coverage config: {has_coverage_config}

Dependency file locations (EXACT paths — use these, do NOT assume root):
{dependency_file_details}

Requirements:
1. Add a new job (e.g. `coverage:`) that uses `needs:` to depend on the existing job.
2. Install dependencies using the EXACT file paths listed above.
3. Run tests with coverage collection enabled for the primary language.
   Use the appropriate coverage tool (e.g. coverage/pytest-cov for Python, nyc/c8 for Node.js,
   JaCoCo for Java, go test -cover for Go, dotnet test --collect for .NET).
4. Generate a coverage report (HTML and/or XML).
5. Upload the coverage report as an artifact.
6. Optionally fail the build if coverage drops below a reasonable threshold.
7. Use the correct test command and working directory for the detected framework.
8. Keep ALL existing content (name, triggers, jobs) unchanged — only ADD the new job.

Output the COMPLETE updated YAML file (the full workflow including the original job + new coverage job).
"""

SONARQUBE_JOB_PROMPT = """You are editing an existing CI pipeline for {platform}.
Add a **sonarqube** job to the workflow below. The sonarqube job MUST depend on the
coverage job (use `needs: coverage` or whatever the coverage job is named).

### Current CI pipeline YAML:
```yaml
{current_ci_yaml}
```

Repository analysis:
- Languages: {languages}
- Frameworks: {frameworks}
- Test frameworks: {test_frameworks}
- Architecture: {architecture}

Dependency file locations (EXACT paths — use these, do NOT assume root):
{dependency_file_details}

Requirements:
1. Add a new job (e.g. `sonarqube:`) that uses `needs:` to depend on the coverage job.
2. Install dependencies and build the project using the EXACT dependency file paths.
3. Download the coverage artifact from the coverage job.
4. Run the SonarQube scanner (sonar-scanner or the appropriate plugin).
5. Use environment variables / secrets for SONAR_HOST_URL and SONAR_TOKEN.
6. Configure the project key as the repository name derived from the repo URL.
7. Include a quality gate check step.
8. Keep ALL existing content (name, triggers, jobs) unchanged — only ADD the new job.

Output the COMPLETE updated YAML file (the full workflow including all existing jobs + new sonarqube job).
"""

RESTRUCTURE_PROMPT = """You have a combined CI pipeline that includes multiple jobs (test, coverage, sonarqube).
Split it into separate production-ready pipeline files for {platform}.

### Combined CI pipeline:
```yaml
{combined_yaml}
```

Create {count} separate pipeline files:
{file_instructions}

Rules for each split file:
- CI pipeline: keep the test/build job as a standalone workflow triggered by push (all branches)
  and pull_request to '{default_branch}'.
- Coverage pipeline: use `workflow_run` trigger that runs after the CI workflow named exactly
  as it appears in the CI file's `name:` field. Use `types: [completed]` and check
  `github.event.workflow_run.conclusion == 'success'`.
- SonarQube pipeline: use `workflow_run` trigger that runs after the Coverage workflow.
  Download the coverage artifact from the triggering run.
- Security pipeline stays unchanged (it's already separate).

Output a JSON array of objects with "filename" and "content" keys. Example:
[{{"filename": ".github/workflows/ci.yml", "content": "name: CI\\n..."}},
 {{"filename": ".github/workflows/coverage.yml", "content": "name: Coverage\\n..."}}]

Output ONLY the JSON array. No markdown fences, no explanations.
"""

COVERAGE_PIPELINE_PROMPT = """Generate a code-coverage pipeline for {platform}.

IMPORTANT: This pipeline MUST depend on the CI pipeline passing first.
{ci_dependency_config}

CRITICAL: When using workflow_run trigger, do NOT add a 'branches:' filter. The pipeline must
be able to trigger on ANY branch (including feature branches) so it can be tested before merging.

Repository analysis:
- Languages: {languages}
- Frameworks: {frameworks}
- Package managers: {package_managers}
- Test frameworks: {test_frameworks}
- Test directories: {test_directories}
- Has existing coverage config: {has_coverage_config}

Dependency file locations (EXACT paths — use these, do NOT assume root):
{dependency_file_details}

Requirements:
1. This pipeline should only run AFTER the CI pipeline succeeds.
2. Install dependencies using the EXACT file paths listed above.
3. Run tests with coverage collection enabled for the primary language.
   Use the appropriate coverage tool (e.g. coverage/pytest-cov for Python, nyc/c8 for Node.js,
   JaCoCo for Java, go test -cover for Go, dotnet test --collect for .NET).
4. Generate a coverage report (HTML and/or XML).
5. Upload the coverage report as an artifact.
6. Optionally fail the build if coverage drops below a reasonable threshold.
7. Use the correct test command and working directory for the detected framework.

Output ONLY the YAML content for the pipeline file.
"""

SONARQUBE_PIPELINE_PROMPT = """Generate a SonarQube analysis pipeline for {platform}.

IMPORTANT: This pipeline MUST depend on the coverage pipeline passing first (so it can use the coverage report).
{coverage_dependency_config}

CRITICAL: When using workflow_run trigger, do NOT add a 'branches:' filter. The pipeline must
be able to trigger on ANY branch (including feature branches) so it can be tested before merging.

Repository analysis:
- Languages: {languages}
- Frameworks: {frameworks}
- Package managers: {package_managers}
- Test frameworks: {test_frameworks}
- Architecture: {architecture}

Dependency file locations (EXACT paths — use these, do NOT assume root):
{dependency_file_details}

Requirements:
1. This pipeline should only run AFTER the coverage pipeline succeeds.
2. Install dependencies and build the project using the EXACT dependency file paths.
3. Run tests with coverage to produce a report SonarQube can consume.
4. Run the SonarQube scanner (sonar-scanner or the appropriate plugin).
5. Use environment variables / secrets for SONAR_HOST_URL and SONAR_TOKEN.
6. Configure the project key as the repository name derived from the repo URL.
7. Include a quality gate check step that waits for the SonarQube quality gate result.

Output ONLY the YAML content for the pipeline file.
"""

SECURITY_PIPELINE_PROMPT = """Generate a security scanning pipeline for {platform}.

Repository analysis:
- Languages: {languages}
- Frameworks: {frameworks}
- Package managers: {package_managers}
- Docker: has_dockerfile={has_dockerfile}, base_images={base_images}
- Architecture: {architecture}

Dependency file locations (EXACT paths — use these, do NOT assume root):
{dependency_file_details}

Requirements:
1. SAST (Static Application Security Testing):
   - Use a tool appropriate for the detected languages (e.g. Semgrep, CodeQL, Bandit for Python, ESLint security plugin for JS/TS).
2. Dependency / SCA scanning:
   - Scan for vulnerable dependencies (e.g. Trivy fs, npm audit, pip-audit, Snyk).
   - Use the EXACT dependency file paths listed above.
3. Container image scanning (if Dockerfiles are present):
   - Build the image and scan it with Trivy or Grype.
4. Secret detection:
   - Scan the codebase for hardcoded secrets (e.g. Gitleaks, TruffleHog).
5. Upload scan results as artifacts.
6. Fail the build on HIGH or CRITICAL severity findings.
7. Trigger on push to ALL branches — do NOT add a `branches:` filter under `push:`.
   For `pull_request:`, filter to the '{default_branch}' branch ONLY.
   CRITICAL: '{default_branch}' is the EXACT, LITERAL branch name — not a description.
   Example trigger block:
     on:
       push:
       pull_request:
         branches: ['{default_branch}']

Output ONLY the YAML content for the pipeline file.
"""

# ── Pipeline Fix Prompt ──────────────────────────────────────────────────────

CLASSIFY_ERROR_PROMPT = """You are a CI/CD pipeline expert. Analyze the error log from a GitHub Actions \
workflow run and classify the ROOT CAUSE into exactly one category.

## Workflow: {workflow_name}

### Pipeline YAML ({filename}):
```yaml
{current_yaml}
```

### Error log:
```
{error_log}
```

## Categories — respond with EXACTLY one word:

- **pipeline_config** — The failure is caused by a pipeline configuration issue that can be fixed \
by editing the YAML. This includes:
  * Wrong file path, wrong command, missing working-directory, wrong action version, bad syntax
  * Missing dependency install step — a package/module that the pipeline should install
    (e.g. Python: ModuleNotFoundError, Node: Cannot find module, Java: package does not exist,
    Go: cannot find package, Ruby: cannot load such file)
  * A linter or static analysis tool that is too strict — fix by adjusting the linter
    configuration in the pipeline command (e.g. relaxing rules, ignoring specific checks,
    adding config flags) rather than removing the linter entirely
  * Missing build tools, compilers, or runtime versions
  * Incorrect install/build commands for the project's language or package manager

- **missing_secret** — The failure is because the workflow needs a secret or environment variable \
that is not configured in GitHub Actions secrets AND cannot be provided in the pipeline itself. \
Examples: missing SONAR_TOKEN, missing cloud provider credentials, missing deployment keys, \
missing API keys for external services that require real authentication. \
NOTE: App config values needed only for tests/builds (e.g. SECRET_KEY, DATABASE_URL, API_BASE_URL) \
CAN be set as env vars directly in the pipeline YAML with dummy/CI values — those are pipeline_config.

- **app_code** — The failure is caused by a bug in the APPLICATION source code that absolutely \
cannot be fixed by changing pipeline YAML. This is ONLY for:
  * A unit test assertion that fails due to application logic (e.g. expected value != actual value)
  * A runtime error in app code during test execution (e.g. null pointer, type error in business logic)
  * NEVER classify linting errors, missing packages, import/require errors, or build tool issues \
as app_code — those are pipeline_config because the pipeline can install packages or adjust tool settings

Respond with ONLY one of: pipeline_config, missing_secret, app_code
"""

FIX_PIPELINE_PROMPT = """A CI/CD pipeline you previously generated is FAILING on {platform}.
This is fix attempt #{fix_attempt}.

## Failing workflow: {workflow_name}

### Current pipeline YAML ({filename}):
```yaml
{current_yaml}
```

### GitHub Actions error log (most recent run):
```
{error_log}
```
{previous_attempts_section}
{applied_fixes_section}
{continue_on_error_section}
### Repository context:
- Languages: {languages}
- Frameworks: {frameworks}
- Package managers: {package_managers}
- Test frameworks: {test_frameworks}
- Test directories: {test_directories}

Dependency file locations (EXACT paths — use these, do NOT assume root):
{dependency_file_details}

## Instructions:
1. Carefully analyze the error log to identify the ROOT CAUSE of the failure.
2. If previous fix attempts are shown above, DO NOT repeat the same fix. The previous approach failed — try a DIFFERENT approach.
3. Fix the pipeline YAML so it passes. Common issues include:
   - Wrong file paths for dependency installation
   - Missing working-directory directives for monorepos / subdirectories
   - Wrong test/build commands for the detected framework
   - Missing environment variables or secrets references
   - Incorrect action versions or syntax errors
   - Missing steps (e.g. checkout, language setup, dependency install)
   - Linter too strict — relax configuration flags instead of removing the linter
   - Missing packages that need to be explicitly installed in the pipeline
4. CRITICAL: If the pipeline uses a 'workflow_run' trigger, do NOT add a 'branches:' filter.
   The pipeline must be able to run on ANY branch (including feature branches).
5. CRITICAL: For push-triggered pipelines, do NOT add a 'branches:' filter under 'push:'.
   For 'pull_request:', filter to the '{default_branch}' branch ONLY.
   NEVER hardcode 'main', 'master', or 'develop'. The default branch is '{default_branch}'.
6. Output the COMPLETE corrected YAML — not a diff, not a partial snippet.

Output ONLY the corrected YAML content. No markdown fences, no explanations.
"""
