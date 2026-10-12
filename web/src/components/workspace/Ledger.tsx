import { cellText, ledgerColumns, looksNumeric } from "../../lib/workspace/ledger";

/** The results table. Columns are whatever keys the agent used, so a new key adds a column. */
export function Ledger({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = ledgerColumns(rows);
  return (
    <div className="ledger-scroll">
      <table className="ledger">
        <caption className="sr">Collected results</caption>
        <thead>
          <tr>
            <th className="n" scope="col">
              #
            </th>
            {columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td className="n">{index + 1}</td>
              {columns.map((column) => {
                const text = cellText(row[column]);
                return (
                  <td key={column} className={looksNumeric(text) ? "num" : undefined}>
                    {text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
