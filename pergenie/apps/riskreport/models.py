from decimal import Decimal

from django.db import models, transaction
from django.db.models import Max
from django.utils import timezone

from celery.decorators import task

from apps.authentication.models import User
from apps.genome.models import Genome, Genotype
from apps.gwascatalog.models import GwasCatalogPhenotype, GwasCatalogSnp
from apps.snp.models import get_freqs
from lib.riskreport.commons import *
from lib.utils.population import POPULATION_MAP
from lib.utils.pg import list2pg_array
from utils import clogging
log = clogging.getColorLogger(__name__)


class RiskReport(models.Model):
    """
     ------------
    |            |
    |   Genome   |
    |            |
     ------------
          1
          |
          n
     ------------       ---------------------       ---------------
    |            |     |                     |     |               |
    | RiskReport |1 - n| PhenotypeRiskReport |1 - n| SnpRiskReport |
    |            |     |                     |     |               |
     ------------       ---------------------       ---------------
                                  1                        1
                                  |                        |
                                  1                        1
                        ----------------------      ----------------
                       |                      |    |                |
                       | GwasCatalogPhenotype |    | GwasCatalogSnp |
                       |                      |    |                |
                        ----------------------      ----------------
    """

    display_id = models.CharField(max_length=8, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    genome = models.ForeignKey(Genome)

    def create_riskreport(self, async=True):
        if async:
            task_create_riskreport.delay(self.id, str(self.genome.id))
        else:
            task_create_riskreport(self.id, str(self.genome.id))


class PhenotypeRiskReport(models.Model):
    risk_report = models.ForeignKey(RiskReport)
    phenotype = models.ForeignKey(GwasCatalogPhenotype)

    estimated_risk = models.DecimalField(max_digits=5, decimal_places=4, null=True)

    class Meta:
        unique_together = ('risk_report', 'phenotype')


class SnpRiskReport(models.Model):
    phenotype_risk_report = models.ForeignKey(PhenotypeRiskReport)
    evidence_snp = models.ForeignKey(GwasCatalogSnp)

    estimated_risk = models.DecimalField(max_digits=5, decimal_places=4, null=True)
    # genotype_ee_risk =
    # genotype_en_risk =
    # genotype_nn_risk =
    # genotype_avg_risk =

    class Meta:
        unique_together = ('phenotype_risk_report', 'evidence_snp')


@task(ignore_result=True)
def task_create_riskreport(risk_report_id, genome_id):  # NOTE: arguments for celery task should be JSON serializable
    risk_report = RiskReport.objects.get(id=risk_report_id)
    genome = Genome.objects.get(id=genome_id)

    log.info('Creating riskreport ...')

    # TODO: Check for updates
    latest_date = GwasCatalogSnp.objects.aggregate(Max('date_downloaded'))['date_downloaded__max']

    phenotypes = GwasCatalogPhenotype.objects.all()
    log.info('#phenotypes: {}'.format(len(phenotypes)))

    population = [genome.population]

    for phenotype in phenotypes:
        assert type(phenotype) == GwasCatalogPhenotype
        gwas_snps = GwasCatalogSnp.objects.filter(phenotype=phenotype,
                                                  population__contains=population,
                                                  date_downloaded=str(latest_date))

        if not gwas_snps:
            continue

        # Select only one article for one phenotype
        #
        # TODO: add conditions
        # - risk alleles are present
        # - odds ratios are present
        # - (beta-coeeff is not present)
        # - lower than minimum p-value
        evidence_article_1st = gwas_snps.exclude(pubmed_id__isnull=True).order_by('reliability_rank').values_list('pubmed_id', flat=True).distinct().first()
        evidence_snps = gwas_snps.filter(pubmed_id=evidence_article_1st)
        evidence_snp_ids = evidence_snps.values_list('snp_id_current', flat=True)

        freqs = get_freqs(evidence_snp_ids, population=population)
        genotypes = Genotype.objects.filter(genome__id=genome.id, rs_id_current__in=evidence_snp_ids)

        phenotype_risk_report, _ = PhenotypeRiskReport.objects.get_or_create(risk_report=risk_report, phenotype=phenotype)

        # Calculate cumulative risk
        estimated_snp_risks = []

        # Genotype specific risks for each SNP
        with transaction.atomic():
            for evidence_snp in evidence_snps:
                # Risk allele and its frequency
                risk_allele_forward = evidence_snp.risk_allele_forward
                risk_allele_freq = freqs.get(evidence_snp.snp_id_current, {}).get(risk_allele_forward)
                odds_ratio = evidence_snp.odds_ratio

                # My genotype
                try:
                    genotype = ''.join(genotypes.get(rs_id_current=evidence_snp.snp_id_current).genotype)
                    zygosities = zyg(genotype, risk_allele_forward)
                except Genotype.DoesNotExist:
                    zygosities = None

                # Genotype specific risks
                if None not in (risk_allele_freq, odds_ratio, zygosities):
                    genotype_specific_risks = genotype_specific_risks_relative_to_population(risk_allele_freq, odds_ratio)
                    my_estimated_risk = estimated_risk(genotype_specific_risks, zygosities)
                else:
                    my_estimated_risk = None

                SnpRiskReport(phenotype_risk_report=phenotype_risk_report,
                              evidence_snp=evidence_snp,
                              estimated_risk=my_estimated_risk).save()

                estimated_snp_risks.append(my_estimated_risk)

            phenotype_risk_report.estimated_risk = cumulative_risk(estimated_snp_risks)
            phenotype_risk_report.save()

    log.info('Done')
