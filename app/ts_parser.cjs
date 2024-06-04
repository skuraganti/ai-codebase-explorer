'use strict';
// Trusted parser only. Input source is never imported, evaluated, emitted or resolved.
const ts = require('./ts_vendor/typescript.js');
let body = '', size = 0;
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { size += Buffer.byteLength(chunk); if (size > 2 * 1024 * 1024) process.exit(2); body += chunk; });
process.stdin.on('end', () => {
 try {
  const {source, tsx = false} = JSON.parse(body);
  if (typeof source !== 'string' || Buffer.byteLength(source) > 512 * 1024) throw Error('Source bound');
  const sf = ts.createSourceFile(tsx ? 'input.tsx' : 'input.ts', source, ts.ScriptTarget.Latest, true, tsx ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const line = pos => sf.getLineAndCharacterOfPosition(pos).line + 1;
  const diagnostics = sf.parseDiagnostics.map(d => ({line: line(d.start || 0), message: ts.flattenDiagnosticMessageText(d.messageText, '\n')})).slice(0, 30);
  const symbols = [], imports = [];
  function addImport(n, specifier, kind, type_only, names = []) {
   imports.push({line: line(n.getStart(sf)), end_line: line(Math.max(n.getStart(sf), n.end - 1)), specifier, statement: n.getText(sf), kind, type_only, names});
  }
  function topLevel(n) { return ts.isSourceFile(n.parent) || ts.isModuleBlock(n.parent); }
  function visit(n, scopes = []) {
   if (symbols.length > 10000 || imports.length > 5000) throw Error('AST output bound');
   const exported = !!n.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword);
   if (ts.isImportDeclaration(n) && ts.isStringLiteral(n.moduleSpecifier)) {
    const clause = n.importClause, bindings = clause?.namedBindings;
    const specs = bindings && ts.isNamedImports(bindings) ? bindings.elements : [];
    const typeOnly = !!clause?.isTypeOnly || (!clause?.name && specs.length > 0 && specs.every(s => s.isTypeOnly));
    addImport(n, n.moduleSpecifier.text, 'import', typeOnly, specs.map(s => s.getText(sf)));
   } else if (ts.isExportDeclaration(n) && n.moduleSpecifier && ts.isStringLiteral(n.moduleSpecifier)) {
    const specs = n.exportClause && ts.isNamedExports(n.exportClause) ? n.exportClause.elements : [];
    addImport(n, n.moduleSpecifier.text, 'reexport', !!n.isTypeOnly || (specs.length > 0 && specs.every(s => s.isTypeOnly)), specs.map(s => s.getText(sf)));
   } else if (ts.isImportEqualsDeclaration(n) && ts.isExternalModuleReference(n.moduleReference) && n.moduleReference.expression) {
    addImport(n, n.moduleReference.expression.text || '(computed)', 'import', !!n.isTypeOnly, [n.name.text]);
   } else if (ts.isCallExpression(n) && n.expression.kind === ts.SyntaxKind.ImportKeyword) {
    const arg = n.arguments[0]; addImport(n, arg && ts.isStringLiteral(arg) ? arg.text : '(computed)', 'dynamic', false);
   }
   let kind = null;
   if (ts.isFunctionDeclaration(n)) kind = 'function';
   else if (ts.isClassDeclaration(n) || ts.isClassExpression(n)) kind = 'class';
   else if (ts.isInterfaceDeclaration(n)) kind = 'interface';
   else if (ts.isTypeAliasDeclaration(n)) kind = 'type_alias';
   else if (ts.isEnumDeclaration(n)) kind = 'enum';
   else if (ts.isModuleDeclaration(n)) kind = 'namespace';
   else if (ts.isMethodDeclaration(n) || ts.isMethodSignature(n) || ts.isGetAccessorDeclaration(n) || ts.isSetAccessorDeclaration(n)) kind = 'method';
   const name = n.name && (ts.isIdentifier(n.name) || ts.isStringLiteral(n.name) || ts.isNumericLiteral(n.name)) ? n.name.text : null;
   if (kind && name) {
    const start = n.getStart(sf), end = Math.max(start, n.end - 1);
    symbols.push({name, qualname: [...scopes, name].join('.'), kind, start_line: line(start), end_line: line(end), signature: n.getText(sf).split('\n').slice(0, 5).join('\n').slice(0, 700), exported: exported && topLevel(n)});
    scopes = [...scopes, name];
   }
   if (ts.isVariableStatement(n) && topLevel(n)) {
    for (const d of n.declarationList.declarations) {
     if (!ts.isIdentifier(d.name)) continue;
     const name = d.name.text;
     symbols.push({name, qualname: [...scopes, name].join('.'), kind: d.initializer && ts.isArrowFunction(d.initializer) ? 'arrow_function' : 'variable', start_line: line(n.getStart(sf)), end_line: line(Math.max(n.getStart(sf), n.end - 1)), signature: n.getText(sf).split('\n').slice(0, 5).join('\n').slice(0, 700), exported});
    }
   }
   if (ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.initializer && (ts.isArrowFunction(n.initializer) || ts.isFunctionExpression(n.initializer))) scopes = [...scopes, n.name.text];
   ts.forEachChild(n, child => visit(child, scopes));
  }
  if (!diagnostics.length) visit(sf);
  const output = JSON.stringify({symbols, imports, diagnostics});
  if (Buffer.byteLength(output) > 4 * 1024 * 1024) throw Error('Serialized AST bound');
  process.stdout.write(output);
 } catch (_) { process.stdout.write(JSON.stringify({symbols: [], imports: [], diagnostics: [{line: 1, message: 'Trusted TypeScript parser failed within its bounds'}]})); process.exitCode = 1; }
});
