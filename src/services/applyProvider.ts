import * as path from "path";
import * as vscode from "vscode";
import { ApplyPreviewResult, ApplyProvider, ApplyResult, BenchOption, WorkspaceContext } from "../types";

type ParsedArtifact = {
  relativePath?: string;
  language?: string;
  content: string;
};

type PendingArtifact = {
  targetUri: vscode.Uri;
  displayPath: string;
  language?: string;
  originalContent: string;
  proposedContent: string;
};

type PendingSession = {
  optionId: string;
  optionTitle: string;
  artifacts: PendingArtifact[];
};

const textEncoder = new TextEncoder();

export class PreviewApplyProvider implements ApplyProvider {
  private pending?: PendingSession;

  async preview(option: BenchOption, workspaceContext: WorkspaceContext): Promise<ApplyPreviewResult> {
    const session = await this.buildSession(option, workspaceContext);
    this.pending = session;

    for (const artifact of session.artifacts) {
      await this.openDiffPreview(session.optionTitle, artifact);
    }

    return {
      optionId: option.id,
      fileCount: session.artifacts.length,
      summary: `Opened preview for ${session.artifacts.length} file${session.artifacts.length === 1 ? "" : "s"}.`
    };
  }

  async applySelected(): Promise<ApplyResult | undefined> {
    if (!this.pending) {
      return undefined;
    }

    const session = this.pending;
    for (const artifact of session.artifacts) {
      const currentContent = await readFileIfExists(artifact.targetUri);
      if (currentContent !== artifact.originalContent) {
        throw new Error(`"${artifact.displayPath}" changed after preview. Re-open the preview before applying.`);
      }

      await ensureParentDirectory(artifact.targetUri);
      await vscode.workspace.fs.writeFile(artifact.targetUri, textEncoder.encode(artifact.proposedContent));
    }

    await this.revealFirstArtifact(session.artifacts[0]);
    this.pending = undefined;

    return {
      optionId: session.optionId,
      fileCount: session.artifacts.length,
      summary: `Applied ${session.artifacts.length} file${session.artifacts.length === 1 ? "" : "s"} to the workspace.`
    };
  }

  async rejectSelected(): Promise<string | undefined> {
    if (!this.pending) {
      return undefined;
    }

    const session = this.pending;
    this.pending = undefined;
    return `Rejected preview for ${session.artifacts.length} file${session.artifacts.length === 1 ? "" : "s"}.`;
  }

  getPendingOptionId(): string | undefined {
    return this.pending?.optionId;
  }

  private async buildSession(option: BenchOption, workspaceContext: WorkspaceContext): Promise<PendingSession> {
    const explicitArtifacts = parseExplicitArtifacts(option.generatedCode);
    const artifacts = explicitArtifacts.some((artifact) => artifact.relativePath)
      ? await this.buildExplicitArtifacts(explicitArtifacts)
      : await this.buildEditorArtifact(explicitArtifacts[0]?.content ?? option.generatedCode, workspaceContext);

    if (!artifacts.length) {
      throw new Error("Bench could not determine where to preview this code.");
    }

    return {
      optionId: option.id,
      optionTitle: option.title,
      artifacts
    };
  }

  private async buildExplicitArtifacts(artifacts: ParsedArtifact[]): Promise<PendingArtifact[]> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (!workspaceRoot) {
      throw new Error("Open a workspace folder before applying file-based code suggestions.");
    }

    const pending: PendingArtifact[] = [];
    for (const artifact of artifacts) {
      if (!artifact.relativePath) {
        continue;
      }

      const targetUri = resolveWorkspacePath(workspaceRoot, artifact.relativePath);
      pending.push({
        targetUri,
        displayPath: normalizeDisplayPath(artifact.relativePath),
        language: artifact.language ?? languageFromPath(artifact.relativePath),
        originalContent: await readFileIfExists(targetUri),
        proposedContent: artifact.content
      });
    }
    return pending;
  }

  private async buildEditorArtifact(code: string, workspaceContext: WorkspaceContext): Promise<PendingArtifact[]> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      throw new Error("Open the file you want Bench to modify, or return code with explicit file paths.");
    }

    const document = editor.document;
    const originalContent = document.getText();
    const selection = editor.selection;
    const proposedContent = selection.isEmpty
      ? insertAtCursor(originalContent, document.offsetAt(selection.active), code)
      : replaceSelection(
          originalContent,
          document.offsetAt(selection.start),
          document.offsetAt(selection.end),
          code
        );

    return [
      {
        targetUri: document.uri,
        displayPath: workspaceContext.activeFileName
          ? vscode.workspace.asRelativePath(workspaceContext.activeFileName, false)
          : path.basename(document.uri.fsPath),
        language: document.languageId,
        originalContent,
        proposedContent
      }
    ];
  }

  private async openDiffPreview(optionTitle: string, artifact: PendingArtifact): Promise<void> {
    const leftDocument = await vscode.workspace.openTextDocument({
      language: artifact.language,
      content: artifact.originalContent
    });
    const rightDocument = await vscode.workspace.openTextDocument({
      language: artifact.language,
      content: artifact.proposedContent
    });

    await vscode.commands.executeCommand(
      "vscode.diff",
      leftDocument.uri,
      rightDocument.uri,
      `Bench Preview: ${artifact.displayPath} (${optionTitle})`,
      {
        preview: true,
        viewColumn: vscode.ViewColumn.Beside
      }
    );
  }

  private async revealFirstArtifact(artifact?: PendingArtifact): Promise<void> {
    if (!artifact) {
      return;
    }

    const document = await vscode.workspace.openTextDocument(artifact.targetUri);
    await vscode.window.showTextDocument(document, { preview: false });
  }
}

function parseExplicitArtifacts(generatedCode: string): ParsedArtifact[] {
  const headingMatches = [...generatedCode.matchAll(/(?:^|\n)###\s+([^\n]+)\n```([^\n]*)\n([\s\S]*?)```/g)];
  if (headingMatches.length) {
    return headingMatches.map((match) => ({
      relativePath: match[1].trim(),
      language: extractLanguage(match[2]),
      content: trimTrailingNewline(match[3])
    }));
  }

  const labeledMatches = [...generatedCode.matchAll(/(?:^|\n)(?:File|Path):\s*([^\n]+)\n```([^\n]*)\n([\s\S]*?)```/g)];
  if (labeledMatches.length) {
    return labeledMatches.map((match) => ({
      relativePath: match[1].trim(),
      language: extractLanguage(match[2]),
      content: trimTrailingNewline(match[3])
    }));
  }

  const fenceMatches = [...generatedCode.matchAll(/```([^\n`]*)\n([\s\S]*?)```/g)];
  if (fenceMatches.length) {
    return fenceMatches.map((match) => ({
      relativePath: extractPathFromFenceInfo(match[1]),
      language: extractLanguage(match[1]),
      content: trimTrailingNewline(match[2])
    }));
  }

  return [{ content: generatedCode.trim() }];
}

function extractLanguage(fenceInfo: string): string | undefined {
  const info = fenceInfo.trim();
  if (!info) {
    return undefined;
  }
  return info.split(/\s+/)[0];
}

function extractPathFromFenceInfo(fenceInfo: string): string | undefined {
  const pathMatch = fenceInfo.match(/(?:path|file|filename|title)=["']?([^"'\s]+)["']?/i);
  if (pathMatch?.[1]) {
    return normalizeDisplayPath(pathMatch[1]);
  }

  for (const token of fenceInfo.split(/\s+/)) {
    const cleaned = token.replace(/^["'`]+|["'`]+$/g, "");
    if (looksLikeRelativePath(cleaned)) {
      return normalizeDisplayPath(cleaned);
    }
  }

  return undefined;
}

function looksLikeRelativePath(value: string): boolean {
  return Boolean(value) && !path.isAbsolute(value) && /[\\/]/.test(value) && /\.[A-Za-z0-9]+$/.test(value);
}

function normalizeDisplayPath(value: string): string {
  return value.replace(/\\/g, "/").replace(/^\.\/+/, "");
}

function resolveWorkspacePath(workspaceRoot: vscode.Uri, relativePath: string): vscode.Uri {
  const normalizedPath = normalizeDisplayPath(relativePath);
  const resolvedPath = path.resolve(workspaceRoot.fsPath, normalizedPath);
  const rootPath = path.resolve(workspaceRoot.fsPath);
  const relative = path.relative(rootPath, resolvedPath);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Bench refused to write outside the workspace: ${normalizedPath}`);
  }
  return vscode.Uri.file(resolvedPath);
}

async function readFileIfExists(uri: vscode.Uri): Promise<string> {
  try {
    const bytes = await vscode.workspace.fs.readFile(uri);
    return new TextDecoder().decode(bytes);
  } catch (error) {
    if (error instanceof vscode.FileSystemError) {
      return "";
    }
    return "";
  }
}

async function ensureParentDirectory(uri: vscode.Uri): Promise<void> {
  const parentUri = vscode.Uri.file(path.dirname(uri.fsPath));
  await vscode.workspace.fs.createDirectory(parentUri);
}

function replaceSelection(source: string, start: number, end: number, code: string): string {
  return `${source.slice(0, start)}${trimTrailingWhitespace(code)}${source.slice(end)}`;
}

function insertAtCursor(source: string, offset: number, code: string): string {
  const trimmedCode = trimTrailingWhitespace(code);
  const prefix = offset > 0 && source[offset - 1] !== "\n" ? "\n" : "";
  const suffix = offset < source.length && source[offset] !== "\n" ? "\n" : "";
  return `${source.slice(0, offset)}${prefix}${trimmedCode}${suffix}${source.slice(offset)}`;
}

function trimTrailingWhitespace(value: string): string {
  return value.replace(/\s+$/u, "");
}

function trimTrailingNewline(value: string): string {
  return value.replace(/\n+$/u, "");
}

function languageFromPath(filePath: string): string | undefined {
  const extension = path.extname(filePath).toLowerCase();
  const map: Record<string, string> = {
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".py": "python",
    ".json": "json",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml"
  };
  return map[extension];
}
