using System.Text;
using FhirLoader;
using Hl7.Fhir.Model;
using Hl7.Fhir.Serialization;

namespace FhirLoader;

internal static class Program
{
    private static int Main(string[] args)
    {
        var opts = Parse(args);
        var map = LoincMap.Load(opts.LoincMap);
        Console.WriteLine($"loaded {map.Entries.Count} LOINC mappings; " +
                          $"{map.Unverified.Count} unverified (see FhirLoader/README.md)");
        foreach (var entry in map.Unverified)
        {
            Console.WriteLine($"  unverified: {entry.Variable} -> {entry.Loinc} ({entry.Note})");
        }

        if (!File.Exists(opts.Csv))
        {
            Console.Error.WriteLine($"missing CSV: {opts.Csv} (run scripts/export_fhirloader.py)");
            return 2;
        }

        var rows = ReadRows(opts.Csv);
        var patients = rows
            .GroupBy(r => r.PatientId, StringComparer.Ordinal)
            .OrderBy(g => g.Key, StringComparer.Ordinal)
            .ToList();
        Console.WriteLine($"read {rows.Count} hourly rows across {patients.Count} patients");

        var selected = opts.Count > 0 ? patients.Take(opts.Count) : patients;
        using var client = new HttpClient { BaseAddress = new Uri(opts.Base + "/") };
        client.DefaultRequestHeaders.Accept.Clear();
        client.DefaultRequestHeaders.TryAddWithoutValidation("Accept", "application/fhir+json");

        long resources = 0;
        foreach (var group in selected)
        {
            int firstIculos = group.Min(r => r.Iculos);
            var patient = PatientMapper.Map(group.First(), new DateOnly(2020, 1, 1));
            var encounter = EncounterMapper.Map(group, group.Key);
            var observations = new List<Observation>(group.Count() * map.Entries.Count);
            foreach (var row in group)
            {
                foreach (var entry in map.Entries)
                {
                    observations.Add(ObservationMapper.Map(row, entry.Variable, entry, firstIculos));
                }
            }

            var allResources = new List<DomainResource> { patient, encounter };
            allResources.AddRange(observations);
            foreach (var bundle in Batch(allResources, opts.Batch))
            {
                Post(client, bundle, opts.Base);
            }

            resources += 2 + observations.Count;
            Console.WriteLine($"  {group.Key}: {observations.Count} observations queued ({resources} resources total)");
        }

        Console.WriteLine($"done. {resources} resources uploaded to {opts.Base}");
        return 0;
    }

    private static IEnumerable<Bundle> Batch(IEnumerable<DomainResource> resources, int batchSize)
    {
        var bucket = new List<DomainResource>();
        foreach (var resource in resources)
        {
            bucket.Add(resource);
            if (bucket.Count >= batchSize)
            {
                yield return BundleBuilder.Create(bucket);
                bucket = new List<DomainResource>();
            }
        }

        if (bucket.Count > 0)
        {
            yield return BundleBuilder.Create(bucket);
        }
    }

    private static void Post(HttpClient client, Bundle bundle, string baseUrl)
    {
        var json = bundle.ToJson();
        using var content = new StringContent(json, Encoding.UTF8, "application/fhir+json");
        var response = client.PostAsync($"fhir", content).GetAwaiter().GetResult();
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException(
                $"HAPI rejected the transaction: HTTP {(int)response.StatusCode}\n{response.Content.ReadAsStringAsync().GetAwaiter().GetResult()}");
        }
    }

    private static List<SourceRow> ReadRows(string csvPath)
    {
        var rows = new List<SourceRow>();
        bool first = true;
        IReadOnlyDictionary<string, int>? map = null;
        foreach (var fields in CsvReader.ReadAll(csvPath))
        {
            if (first)
            {
                map = CsvColumns.FromHeader(fields);
                first = false;
                continue;
            }

            var m = map!;
            var vitals = new Dictionary<string, decimal?>(StringComparer.Ordinal);
            foreach (var variable in ClinicalVariables.All)
            {
                int column = CsvColumns.Required(m, variable);
                vitals[variable] = CsvColumns.Decimal(fields[column]);
            }

            rows.Add(new SourceRow(
                PatientId: fields[CsvColumns.Required(m, "patient_id")],
                Site: fields[CsvColumns.Required(m, "site")],
                Hour: CsvColumns.Int(fields[CsvColumns.Required(m, "hour")]),
                Iculos: CsvColumns.Int(fields[CsvColumns.Required(m, "ICULOS")]),
                Age: CsvColumns.Decimal(fields[CsvColumns.Required(m, "Age")]),
                Gender: CsvColumns.Decimal(fields[CsvColumns.Required(m, "Gender")]) is decimal gd
                    ? (int)gd
                    : (int?)null,
                Unit1: CsvColumns.Decimal(fields[CsvColumns.Required(m, "Unit1")]),
                Unit2: CsvColumns.Decimal(fields[CsvColumns.Required(m, "Unit2")]),
                SepsisLabel: CsvColumns.Bool(fields[CsvColumns.Required(m, "SepsisLabel")]),
                Vitals: vitals));
        }

        return rows;
    }

    private static Options Parse(string[] args)
    {
        var opts = new Options();
        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--csv": opts.Csv = args[++i]; break;
                case "--base": opts.Base = args[++i]; break;
                case "--count": opts.Count = int.Parse(args[++i]); break;
                case "--batch": opts.Batch = int.Parse(args[++i]); break;
                case "--loinc": opts.LoincMap = args[++i]; break;
                default:
                    Console.Error.WriteLine($"unknown option {args[i]}");
                    Console.Error.WriteLine("usage: FhirLoader --csv <path> [--base <fhir-url>] " +
                                            "[--count N] [--batch N] [--loinc <json>]");
                    Environment.Exit(2);
                    break;
            }
        }

        return opts;
    }

    private sealed class Options
    {
        public string Csv { get; set; } = Path.Combine("data", "interim", "fhirloader.csv");
        public string Base { get; set; } = "http://localhost:8080";
        public string LoincMap { get; set; } = Path.Combine("src", "FhirLoader", "LoincMap.json");
        public int Count { get; set; }
        public int Batch { get; set; } = 500;
    }
}

/// <summary>Frozen list of the clinical variables and the study label, in CSV header order.</summary>
public static class ClinicalVariables
{
    public static readonly string[] All =
    {
        "HR", "O2Sat", "Temp", "SBP", "MAP", "DBP", "Resp", "EtCO2",
        "BaseExcess", "HCO3", "FiO2", "pH", "PaCO2", "SaO2", "AST", "BUN",
        "Alkalinephos", "Calcium", "Chloride", "Creatinine", "Bilirubin_direct",
        "Glucose", "Lactate", "Magnesium", "Phosphate", "Potassium",
        "Bilirubin_total", "TroponinI", "Hct", "Hgb", "PTT", "WBC", "Fibrinogen",
        "Platelets",
        // Study annotation, not a clinical measurement (§7 dashboard onset marker).
        "SepsisLabel",
    };
}