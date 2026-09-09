using Hl7.Fhir.Model;

namespace FhirLoader;

/// <summary>
/// Assembles FHIR transaction Bundles whose entries are all conditional
/// creates: every resource carries an origin identifier and every entry uses
/// request.ifNoneExist on that identifier, so re-uploading is a no-op
/// (idempotent — the server ignores a second identical create).
/// </summary>
public static class BundleBuilder
{
    /// <summary>Build a transaction bundle from arbitrary R4 domain resources.</summary>
    public static Bundle Create(IEnumerable<DomainResource> resources)
    {
        var bundle = new Bundle
        {
            Type = Bundle.BundleType.Transaction,
            Entry = new List<Bundle.EntryComponent>(),
        };

        foreach (var resource in resources)
        {
            bundle.Entry.Add(new Bundle.EntryComponent
            {
                Resource = resource,
                Request = new Bundle.RequestComponent
                {
                    Method = Bundle.HTTPVerb.POST,
                    Url = resource.TypeName,
                    IfNoneExist = IfNoneExist(resource),
                },
            });
        }

        return bundle;
    }

    private static string IfNoneExist(DomainResource resource)
    {
        // Patient, Encounter and Observation all declare a List<Identifier>;
        // the base DomainResource class does not expose it, hence the bounded
        // reflection. If a new resource type without an origin identifier is
        // ever added here, it will be refused loudly, not silently.
        var identifier = resource.GetType()
            .GetProperty("Identifier")?
            .GetValue(resource) as List<Identifier> ?? new List<Identifier>();

        var origin = identifier.FirstOrDefault(i => i.System != null && i.System.StartsWith("urn:project:sepsis-cds:origin", StringComparison.Ordinal));
        if (origin is null)
        {
            throw new ArgumentException(
                $"{resource.TypeName} has no origin identifier; every uploaded resource must have one");
        }

        return $"identifier={origin.System}|{origin.Value}";
    }
}