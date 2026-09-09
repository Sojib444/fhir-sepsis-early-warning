using System.Globalization;

namespace FhirLoader;

/// <summary>
/// Boring hand-rolled CSV reader: LF or CRLF, quoted fields tolerated,
/// empty cell means null. The exporter writes exactly this shape
/// (scripts/export_fhirloader.py), so we do not need a third-party CSV
/// dependency.
/// </summary>
public static class CsvReader
{
    public static IEnumerable<string[]> ReadAll(string path)
    {
        using var reader = new StreamReader(path);
        var line = reader.ReadLine();
        if (line is null)
        {
            yield break;
        }

        var header = ParseLine(line);
        while ((line = reader.ReadLine()) is not null)
        {
            var fields = ParseLine(line);
            if (fields.Length < header.Length)
            {
                throw new InvalidDataException(
                    $"CSV row has {fields.Length} fields, expected at least {header.Length}");
            }

            yield return fields;
        }
    }

    public static string[] ParseLine(string line)
    {
        var fields = new List<string>();
        var current = new System.Text.StringBuilder();
        bool inQuotes = false;
        for (int i = 0; i < line.Length; i++)
        {
            char c = line[i];
            if (inQuotes)
            {
                if (c == '"' && i + 1 < line.Length && line[i + 1] == '"')
                {
                    current.Append('"');
                    i++;
                }
                else if (c == '"')
                {
                    inQuotes = false;
                }
                else
                {
                    current.Append(c);
                }
            }
            else if (c == '"' && current.Length == 0)
            {
                inQuotes = true;
            }
            else if (c == ',')
            {
                fields.Add(current.ToString());
                current.Clear();
            }
            else
            {
                current.Append(c);
            }
        }

        fields.Add(current.ToString());
        return fields.ToArray();
    }
}

/// <summary>Column index map for the exporter's CSV header.</summary>
public static class CsvColumns
{
    public static IReadOnlyDictionary<string, int> FromHeader(string[] header)
    {
        var map = new Dictionary<string, int>(StringComparer.Ordinal);
        for (int i = 0; i < header.Length; i++)
        {
            map[header[i].Trim()] = i;
        }

        return map;
    }

    public static int Required(IReadOnlyDictionary<string, int> map, string column) =>
        map.TryGetValue(column, out var i)
            ? i
            : throw new InvalidDataException($"CSV is missing required column '{column}'");

    public static decimal? Decimal(string cell) =>
        cell.Length == 0 ? null : decimal.Parse(cell, CultureInfo.InvariantCulture);

    public static int Int(string cell) => int.Parse(cell, CultureInfo.InvariantCulture);

    public static bool Bool(string cell) => cell == "1";
}