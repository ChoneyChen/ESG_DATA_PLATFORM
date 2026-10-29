"""Report resource identity is PDF-level, distinct from measurement identity."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator,JsonValue

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)

class Company(Model):
    name:str=Field(min_length=1)
    company_id:str | None=None

class Industry(Model):
    name:str=Field(min_length=1)
    code:str | None=None
    taxonomy:str | None=None

class ReportResource(Model):
    schema_version:Literal['report-resource-1.0']='report-resource-1.0'
    pdf_id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
    asset_id:str=Field(pattern=r'^pdf-[a-f0-9]{24}$')
    sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    original_filename:str=Field(min_length=1)
    company:Company | None=None
    publication_year:int | None=Field(default=None,ge=1900,le=2200)
    industry:Industry | None=None
    reporting_year:int | None=Field(default=None,ge=1900,le=2200)
    title:str | None=None
    metadata_sources:dict[str,str]=Field(default_factory=dict)
    extensions:dict[str,JsonValue]=Field(default_factory=dict)
    revision:int=Field(default=1,ge=1)
    @model_validator(mode='after')
    def same_pdf(self):
        if self.asset_id!='pdf-'+self.sha256[:24]:raise ValueError('Asset identity differs from PDF content')
        return self
    @property
    def missing_metadata(self):
        return [name for name in ('company','publication_year','industry') if getattr(self,name) is None]

class BatchItem(Model):
    pdf_id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')
    file:str=Field(min_length=1)
    sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    company:Company
    publication_year:int=Field(ge=1900,le=2200)
    industry:Industry
    reporting_year:int | None=Field(default=None,ge=1900,le=2200)
    title:str | None=None
    metadata_sources:dict[str,str]=Field(default_factory=dict)
    extensions:dict[str,JsonValue]=Field(default_factory=dict)

class BatchManifest(Model):
    schema_version:Literal['report-resource-batch-1.0']='report-resource-batch-1.0'
    reports:list[BatchItem]=Field(min_length=1,max_length=10000)
    @model_validator(mode='after')
    def unique_pdf(self):
        for key in ('pdf_id','file','sha256'):
            values=[getattr(item,key) for item in self.reports]
            if len(values)!=len(set(values)):raise ValueError('Duplicate batch '+key)
        return self
