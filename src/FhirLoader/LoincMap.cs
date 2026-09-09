using System.Globalization;
using System.Text.Json;

namespace FhirLoader;

/// <summary>One LOINC entry for a clinical variable (keyed by the source column).</summary>
public sealed record LoincEntry(
    string Variable,
    string Loinc,
    string Display,
    string Ucum,
    bool Verified,
    string Note);

/// <summary>Parsed copy of LoincMap.json, plus the identifier systems used for idempotency.</summary>
public sealed class LoincMap
{
    public const string LoincSystem = "http://loinc.org";
    public const string UcumSystem = "http://unitsofmeasure.org";

    /// <summary>Identifiers on every generated resource; used for conditional creates.</summary>
    public const string OriginSystem = "urn:project:sepsis-cds:origin";

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public IReadOnlyList<LoincEntry> Entries { get; }

    private readonly Dictionary<string, LoincEntry> _byVariable;

    public static LoincMap Load(string path)
    {
        using var stream = File.OpenRead(path);
        return new LoincMap(JsonSerializer.Deserialize<Doc>(stream, JsonOptions)
                    ?? throw new InvalidDataException("empty loinc map"));
    }

    public LoincEntry For(string variable) =>
        _byVariable.TryGetValue(variable, out var e) ? e : throw new KeyNotFoundException(variable);

    public IReadOnlyList<LoincEntry> Unverified =>
        Entries.Where(e => !e.Verified).ToList();

    private LoincMap(Doc doc)
    {
        Entries = doc.Variables
            .OrderBy(pair => pair.Key, StringComparer.Ordinal)
            .Select(pair => new LoincEntry(
                pair.Key,
                pair.Value.Loinc,
                pair.Value.Display,
                pair.Value.Ucum,
                pair.Value.Verified,
                pair.Value.Note ?? string.Empty))
            .ToList();
        _byVariable = Entries.ToDictionary(e => e.Variable, StringComparer.Ordinal);
    }

    private sealed class Doc
    {
        public Dictionary<string, Entry> Variables { get; set; } = new();
    }

    private sealed class Entry
    {
        public string Loinc { get; set; } = string.Empty;
        public string Display { get; set; } = string.Empty;
        public string Ucum { get; set; } = string.Empty;
        public bool Verified { get; set; }
        public string? Note { get; set; }
    }

    /// <summary>Format a number with the basket-case precision the source uses (two decimals).</summary>
    public static decimal Normalize(string? raw) =>
        raw is null ? throw new FormatException("null is not a number") : decimal.Parse(raw, CultureInfo.InvariantCulture);
}